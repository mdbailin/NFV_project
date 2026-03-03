FROM ubuntu:jammy

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
	iptables-persistent \
	iproute2 \
	iptables \
	wondershaper \
	tcpdump \
 && rm -rf /var/lib/apt/lists/*

COPY docker/init_fw.sh /init_fw.sh
COPY docker/init_nat.sh /init_nat.sh
RUN chmod +x /init_fw.sh /init_nat.sh
