import importlib.util
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DnsRedirectTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("dns_nft_test", "modules/dns_nft.py")

    def test_redirect_uses_idempotent_prerouting_rules(self):
        commands = []

        def fake_run(args, timeout=10):
            commands.append(args)
            if args[:5] == ["-a", "list", "chain", "ip", "nat"]:
                return True, "chain PREROUTING { }", ""
            return True, "", ""

        self.module._run_nft = fake_run
        ok, message = self.module.setup_dns_redirect(
            "wan0", ["lan0"], gateway_ip="192.168.50.1"
        )

        self.assertTrue(ok, message)
        add_rules = [command for command in commands if command[:3] == ["add", "rule", "ip"]]
        self.assertEqual(len(add_rules), 2)
        self.assertTrue(all(command[3:6] == ["nat", "PREROUTING", "iifname"] for command in add_rules))
        self.assertTrue(all("aegisgate_dns_redirect" in command for command in add_rules))

    def test_remove_deletes_only_tagged_handles(self):
        commands = []

        def fake_run(args, timeout=10):
            commands.append(args)
            if args[0] == "-a":
                return True, (
                    'udp dport 53 comment "aegisgate_dns_redirect" # handle 41\n'
                    'tcp dport 22 accept # handle 42\n'
                    'tcp dport 53 comment "aegisgate_dns_redirect" # handle 43'
                ), ""
            return True, "", ""

        self.module._run_nft = fake_run
        ok, message = self.module.remove_dns_redirect()

        self.assertTrue(ok, message)
        deletes = [command for command in commands if command[:2] == ["delete", "rule"]]
        self.assertEqual([command[-1] for command in deletes], ["41", "43"])


class HealthRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("health_monitor_test", "scripts/health-monitor.py")
        self.module.FAILURES.clear()
        self.module.LAST_RECOVERY.clear()

    def test_nft_and_network_failures_trigger_bounded_recovery(self):
        checks = {
            "dashboard": {"ok": True},
            "dns": {"ok": True, "expected": True},
            "dhcp": {"ok": True, "expected": False},
            "wireguard": {"ok": True, "expected": False, "interface": "wg0"},
            "qos": {"ok": True, "expected": False},
            "nftables": {"ok": False},
            "wan": {"ok": False},
            "lan": {"ok": True},
        }
        self.module.FAILURES.update({"nftables": self.module.FAILURE_THRESHOLD - 1,
                                     "wan": self.module.FAILURE_THRESHOLD - 1})
        restarted = []

        def fake_restart(check_name, service):
            restarted.append((check_name, service))
            self.module.LAST_RECOVERY[check_name] = 1
            return {"check": check_name, "service": service, "ok": True}

        self.module.restart_service = fake_restart
        actions = self.module.recover(checks)

        self.assertEqual(
            restarted,
            [("nftables", "nftables.service"),
             ("network", "aegisgate-net-setup.service")],
        )
        self.assertEqual(len(actions), 2)


class RestoreStateTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("restore_state_test", "restore-state.py")

    def test_main_fails_closed_when_firewall_restore_fails(self):
        with mock.patch.object(self.module, "_get_ifaces_from_config", return_value=("wan0", "lan0", "", "", "")), \
                mock.patch.object(self.module, "wait_for_interface", return_value=True), \
                mock.patch.object(self.module, "restore_nftables", return_value=False), \
                mock.patch.object(self.module.time, "sleep"):
            self.assertEqual(self.module.main(), 1)


if __name__ == "__main__":
    unittest.main()
