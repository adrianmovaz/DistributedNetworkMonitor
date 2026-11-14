# Requirements and acceptance checklist

Source: `PROYECTO FINAL.pdf`, all seven pages. This repository is a starting
foundation, not a completed final submission.

## Functional scope

| PDF requirement | Foundation now | Remaining work |
| --- | --- | --- |
| HTTP device list/status, incidents, statistics, monitoring requests, reports | Raw socket GET/POST, persistent registry, stored `unknown` status | Actual monitoring status, incidents, statistics, jobs, reports, browser UI |
| FTP connect/authenticate/list/download/upload in passive mode | External FTP lab; adapter interface | Socket control/data channels and commands; file integrity checks |
| Real SNMP device/interface/traffic queries and result recording | External Net-SNMP agent; PySNMP dependency; adapter interface | Queries, timeout detection and persisted results |
| SMTP down/recovery/abnormal/report notifications | Mailpit lab; adapter interface | Email composition, sending, retries and integration |
| DNS A/AAAA/MX, nonexistent names and resolution errors | Deterministic DNS lab; dnspython dependency; adapter interface | Queries and integration with registered hostnames |
| Central incident → notification → report → FTP → HTTP workflow | Shared registry and architecture plan | Coordinator, incident/result storage and delivery tracking |
| Blocking threads and nonblocking `select()` | Both HTTP modes implemented | Compare under monitoring load; keep slow work off select loop |
| At least three justified synchronization mechanisms | Lock, BoundedSemaphore and Event | Extend justification/tests to monitoring, incidents and transfers |

## Ten mandatory test scenarios

| # | Scenario | Current coverage / evidence still needed |
| --- | --- | --- |
| 1 | At least 10 simultaneous clients | Automated real TCP test in both modes |
| 2 | At least 20 requests over one connection | Automated test checks socket identity in both modes |
| 3 | Concurrent monitoring of multiple devices | Pending real SNMP adapters/coordinator |
| 4 | Simultaneous failures without lost incidents | Pending atomic result/status/incident storage |
| 5 | SMTP incident notifications | Pending; inspect messages in Mailpit |
| 6 | FTP report transfer with identical received file | Pending; compare SHA-256 hashes |
| 7 | Valid DNS records and resolution errors | Lab configured; application integration tests pending |
| 8 | Unexpected client disconnection | Automated incomplete-request disconnect test in both modes; add disconnect during monitoring |
| 9 | Concurrent shared-state consistency | Automated concurrent registration/persistence tests; extend to status and incident transactions |
| 10 | Compare both modalities for scenarios 1 and 2 | Both exercised automatically; timing/resource measurements and written comparison pending |

## Submission deliverables

- [x] Organized source foundation and installation/run instructions.
- [x] Initial architecture and connection flow diagrams.
- [ ] Complete source implementing all five integrated protocols.
- [ ] Technical document explaining all five protocols: message formats, command
  exchanges, responses, OIDs, record types and observed errors.
- [ ] Final concurrency/synchronization explanation with critical sections and
  consequences of removing each primitive.
- [ ] Saved execution evidence for all ten scenarios, including both modes and
  real laboratory network operations. Use timestamped logs, packet captures,
  inbox screenshots, report hashes and comparison measurements.
- [ ] Functional integrated demonstration; every team member must be able to
  explain the architecture, protocol messages and synchronization choices.
