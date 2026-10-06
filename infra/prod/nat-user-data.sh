#!/bin/bash
set -euxo pipefail
# The instance may not have its public IP routed yet at first boot; retry.
for i in $(seq 1 30); do
  dnf install -y iptables-services && break
  sleep 10
done
rpm -q iptables-services
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/90-nat.conf
sysctl --system
IFACE=$(ip route show default | awk '{print $5; exit}')
iptables -F FORWARD
iptables -t nat -F POSTROUTING
iptables -t nat -A POSTROUTING -o "$IFACE" -s ${vpc_cidr} -j MASQUERADE
iptables-save > /etc/sysconfig/iptables
systemctl enable --now iptables
