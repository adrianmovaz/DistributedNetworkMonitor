FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends snmpd snmp dnsmasq dnsutils \
    && rm -rf /var/lib/apt/lists/*
COPY snmpd.conf /etc/snmp/snmpd.conf
COPY dnsmasq.conf /etc/dnsmasq.conf
