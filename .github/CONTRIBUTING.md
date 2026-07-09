# Contributing to AgriTrust

Thanks for your interest in contributing! AgriTrust is an AI-powered RWA invoice financing protocol on Casper Blockchain.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone git@github.com:YOURUSERNAME/agritrust-casper.git`
3. Create a branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Run tests: `cd contracts && cargo test`
6. Commit: `git commit -m 'feat: describe your change'`
7. Push: `git push origin feature/your-feature-name`
8. Open a Pull Request

## Commit Convention

We use [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `refactor:` code change that neither fixes a bug nor adds a feature
- `test:` adding or correcting tests
- `chore:` build/tooling changes

## Code Style

- **Rust (contracts):** Follow `rustfmt` defaults. Run `cargo fmt` before committing.
- **JavaScript/TypeScript:** Use Prettier defaults.
- **Python (scripts):** Follow PEP 8.

## Reporting Issues

Use GitHub Issues. Include:
- Clear description of the issue
- Steps to reproduce
- Expected vs actual behavior
- Casper testnet transaction hashes (if applicable)

## Security

Found a vulnerability? Email ubongnt@gmail.com. Do NOT open a public issue.
