# Contributing to AegisGate

Thanks for your interest in improving AegisGate! 🛡️

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/AegisGate.git
   cd AegisGate
   ```
3. **Create a branch** for your work:
   ```bash
   git checkout -b feature/my-feature
   ```

## Development Setup

AegisGate runs on bare-metal Linux (nftables, dnsmasq, Suricata require root + kernel access). For UI / Python module development:

```bash
# Install Python dependencies
sudo apt install python3-flask python3-gunicorn python3-requests python3-bcrypt

# Run the dashboard in development mode
cd /opt/nft-dashboard
python3 -c "from app import app; app.run(host='0.0.0.0', port=8080, debug=True)"
```

> ⚠️ **Warning:** Never run the installer or firewall modules on a production gateway without testing first. Use a VM or dedicated test machine.

## Code Style

- **Python:** PEP 8, 4-space indent, descriptive names
- **Jinja2 templates:** 2-space indent, follow existing `base.html` structure
- **Shell scripts:** `bash -n` must pass, use `set -euo pipefail` where appropriate
- **Dual theme:** All UI changes must work in both light and dark themes — use CSS variables (`var(--bg)`, `var(--text)`, etc.)
- **No secrets:** Never commit passwords, tokens, real IPs, or production config. Use `config.example.json` for samples.

## Commit Messages

Use clear, descriptive commit messages:

```
Add GeoIP filtering for forwarded traffic

- Insert geoip match in forward chain before lan_trusted accept
- Add /geoip/lookup API endpoint
- Update firewall.html with country selector
```

## Pull Requests

1. **Test** your changes — `bash -n install.sh`, `python3 -m py_compile modules/*.py`
2. **Self-review** your diff before opening a PR
3. **Describe** what changed and why
4. **Link** any related issues

## Reporting Issues

Use [GitHub Issues](https://github.com/PentestForge/AegisGate/issues) for:

- 🐛 Bugs — include OS, steps to reproduce, logs
- 💡 Feature requests — describe the use case
- 📖 Documentation improvements

## Security Reports

Found a security issue in AegisGate itself? Please **do not** open a public issue.

Email: **security@pentest-forge.com**

## License

By contributing, you agree that your contributions will be licensed under the MIT License.