"""Explicit blocking/threaded and nonblocking/select socket servers."""

import errno
import logging
import select
import socket
import threading
import time
from dataclasses import dataclass, field

from network_monitor.config import Settings
from network_monitor.web.http import Application, BadRequest, parse_request, response

LOG = logging.getLogger(__name__)


@dataclass
class Connection:
    incoming: bytes = b""
    outgoing: bytes = b""
    closing: bool = False
    touched: float = field(default_factory=time.monotonic)


class Server:
    def __init__(self, settings: Settings, application: Application):
        self.settings = settings
        self.application = application
        self.stopping = threading.Event()
        self._serving = threading.Event()
        self._closed = threading.Event()
        self._serve_thread = None
        self._slots = threading.BoundedSemaphore(settings.max_clients)
        self._clients = set()
        self._clients_lock = threading.Lock()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind((settings.host, settings.port))
            self.listener.listen(settings.max_clients)
            self.address = self.listener.getsockname()
        except OSError:
            self.listener.close()
            raise

    def stop(self):
        self.stopping.set()

    def shutdown(self):
        self.stop()
        if self._serving.is_set() and threading.current_thread() is not self._serve_thread:
            self._closed.wait()
        else:
            self.listener.close()

    def serve_forever(self):
        self._serve_thread = threading.current_thread()
        self._serving.set()
        try:
            if self.settings.mode == "threaded":
                self._serve_threaded()
            elif self.settings.mode == "select":
                self._serve_select()
            else:
                raise ValueError("Unknown server mode")
        finally:
            self.stopping.set()
            self.listener.close()
            self._serving.clear()
            self._closed.set()

    def _dispatch(self, state: Connection):
        try:
            while not state.closing:
                parsed = parse_request(state.incoming)
                if parsed is None:
                    break
                request, state.incoming = parsed
                state.outgoing += self.application.handle(request)
                state.closing = request.close
                # Bound queued responses if a client pipelines without reading.
                if len(state.outgoing) > 1024 * 1024:
                    state.closing = True
        except BadRequest as exc:
            state.outgoing += response(400, {"error": str(exc)}, close=True)
            state.closing = True
        except Exception:
            LOG.exception("Unexpected HTTP request handler failure")
            state.outgoing += response(
                500, {"error": "No se pudo procesar la solicitud."}, close=True
            )
            state.closing = True

    def _serve_threaded(self):
        self.listener.settimeout(0.2)
        workers = []
        try:
            while not self.stopping.is_set():
                try:
                    client, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError as exc:
                    if self.stopping.is_set():
                        break
                    if exc.errno in {errno.ECONNABORTED, errno.ECONNRESET, errno.EINTR}:
                        continue
                    raise
                # Nonblocking admission: an idle client cannot create unlimited threads.
                if not self._slots.acquire(blocking=False):
                    client.close()
                    continue
                with self._clients_lock:
                    self._clients.add(client)
                worker = threading.Thread(target=self._handle_client, args=(client,))
                try:
                    worker.start()
                except Exception:
                    with self._clients_lock:
                        self._clients.discard(client)
                    client.close()
                    self._slots.release()
                    LOG.exception("Unable to start HTTP client thread")
                    continue
                workers = [thread for thread in workers if thread.is_alive()]
                workers.append(worker)
        finally:
            with self._clients_lock:
                for client in self._clients:
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            for worker in workers:
                worker.join()

    def _handle_client(self, client):
        state = Connection()
        try:
            client.settimeout(self.settings.socket_timeout)
            while not self.stopping.is_set():
                data = client.recv(4096)
                if not data:
                    break
                state.incoming += data
                self._dispatch(state)
                if state.outgoing:
                    client.sendall(state.outgoing)
                    state.outgoing = b""
                if state.closing:
                    break
        except OSError:
            LOG.debug("Client disconnected or timed out", exc_info=True)
        finally:
            with self._clients_lock:
                self._clients.discard(client)
            client.close()
            self._slots.release()

    def _serve_select(self):
        self.listener.setblocking(False)
        clients = {}

        def discard(client):
            client.close()
            del clients[client]

        try:
            while not self.stopping.is_set():
                readable = [self.listener] + [c for c, s in clients.items() if not s.closing]
                writable = [c for c, s in clients.items() if s.outgoing]
                ready_read, ready_write, _ = select.select(readable, writable, [], 0.2)
                for client in ready_read:
                    if client is self.listener:
                        try:
                            connection, _ = self.listener.accept()
                        except OSError as exc:
                            if exc.errno in {
                                errno.EAGAIN,
                                errno.EWOULDBLOCK,
                                errno.ECONNABORTED,
                                errno.EINTR,
                            }:
                                continue
                            if self.stopping.is_set():
                                break
                            raise
                        connection.setblocking(False)
                        try:
                            select.select([connection], [], [], 0)
                        except ValueError:
                            connection.close()
                            LOG.warning("Rejecting client beyond select descriptor capacity")
                            continue
                        if len(clients) >= self.settings.max_clients:
                            connection.close()
                        else:
                            clients[connection] = Connection()
                        continue
                    state = clients[client]
                    try:
                        data = client.recv(4096)
                        if not data:
                            # Preserve already queued replies after a client half-closes.
                            state.closing = True
                            if not state.outgoing:
                                discard(client)
                            continue
                        state.touched = time.monotonic()
                        state.incoming += data
                        self._dispatch(state)
                    except BlockingIOError:
                        pass
                    except OSError:
                        discard(client)
                for client in ready_write:
                    if client not in clients:
                        continue
                    state = clients[client]
                    try:
                        sent = client.send(state.outgoing)
                        if sent == 0:
                            discard(client)
                            continue
                        state.outgoing = state.outgoing[sent:]
                        state.touched = time.monotonic()
                        if not state.outgoing and state.closing:
                            discard(client)
                    except BlockingIOError:
                        pass
                    except OSError:
                        discard(client)
                now = time.monotonic()
                for client, state in list(clients.items()):
                    if now - state.touched > self.settings.socket_timeout:
                        discard(client)
        finally:
            for client in clients:
                client.close()
