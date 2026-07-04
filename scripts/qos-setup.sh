#!/bin/bash
ethtool -K eth1 gro on 2>/dev/null
ethtool -K wg0 gro on 2>/dev/null
echo f > /sys/class/net/eth1/queues/rx-0/rps_cpus 2>/dev/null
echo f > /sys/class/net/wg0/queues/rx-0/rps_cpus 2>/dev/null
tc qdisc del dev eth1 root 2>/dev/null
for i in 1 2 3 4 5 6 7 8; do tc qdisc del dev eth1 parent 1:$i 2>/dev/null; done
tc qdisc add dev eth1 root handle 1: cake bandwidth 806Mbit ethernet diffserv4 dual-srchost nat nowash no-ack-filter split-gso rtt 50ms noatm overhead 38 mpu 84 2>/dev/null || tc qdisc replace dev eth1 root handle 1: cake bandwidth 806Mbit ethernet diffserv4 dual-srchost nat nowash no-ack-filter split-gso rtt 50ms noatm overhead 38 mpu 84 2>/dev/null
tc qdisc del dev wg0 root 2>/dev/null
for i in 1 2 3 4 5 6 7 8; do tc qdisc del dev wg0 parent 1:$i 2>/dev/null; done
tc qdisc add dev wg0 root handle 1: cake bandwidth 806Mbit ethernet diffserv4 dual-srchost nat nowash no-ack-filter split-gso rtt 50ms noatm overhead 38 mpu 84 2>/dev/null || tc qdisc replace dev wg0 root handle 1: cake bandwidth 806Mbit ethernet diffserv4 dual-srchost nat nowash no-ack-filter split-gso rtt 50ms noatm overhead 38 mpu 84 2>/dev/null
/usr/sbin/nft delete table inet qos_marks 2>/dev/null
cat <<'NFT_EOF' | /usr/sbin/nft -f -
table inet qos_marks {
  chain mark_forward {
    type filter hook forward priority mangle; policy accept;
    udp dport { 5060-5061,10000-20000,3478 } ip dscp set cs5
    udp sport { 5060-5061,10000-20000,3478 } ip dscp set cs5
    tcp dport { 20-21,69,119,445,873,3389 } ip dscp set cs1
    tcp sport { 20-21,69,119,445,873,3389 } ip dscp set cs1
    udp dport { 20-21,69,119,445,873,3389 } ip dscp set cs1
    udp sport { 20-21,69,119,445,873,3389 } ip dscp set cs1
  }
}
NFT_EOF
tc filter del dev eth1 parent 1: 2>/dev/null
tc filter del dev wg0 parent 1: 2>/dev/null
tc filter add dev eth1 parent 1: protocol ip prio 10 u32 match ip dport 22 0xffff action police rate 450000kbit burst 900000kbit conform-exceed pass/continue 2>/dev/null
tc filter add dev wg0 parent 1: protocol ip prio 10 u32 match ip dport 22 0xffff action police rate 450000kbit burst 900000kbit conform-exceed pass/continue 2>/dev/null
