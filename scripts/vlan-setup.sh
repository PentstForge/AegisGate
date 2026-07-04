#!/bin/bash
ip link add link eth1 name eth1.100 type vlan id 100 2>/dev/null
ip addr add 192.168.100.1/24 dev eth1.100 2>/dev/null
ip link set eth1.100 up 2>/dev/null
