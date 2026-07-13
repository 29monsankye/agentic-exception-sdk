# Contributing to agentic-exception-sdk

Thanks for your interest in contributing! This document explains how to set up
a development environment, the standards we hold changes to, and the sign-off
we require on every commit.

## Code of Conduct

This project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By
participating, you agree to uphold it. Please report unacceptable behavior to
**29monsank@gmail.com**.

## Reporting security issues

Do **not** open a public issue for security vulnerabilities. Follow the private
process in [SECURITY.md](SECURITY.md) instead.

## Development setup

Requires Python 3.11+.

```bash
git clone https://github.com/29monsankye/agentic-exception-sdk.git
cd agentic-exception-sdk
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all]"
```

## Checks (run before opening a PR)

```bash
pytest -q            # tests must pass
ruff check .         # lint must be clean
mypy src             # type-check must be clean for the core package
```

All three are expected to be green. New behavior should ship with tests; bug
fixes should include a regression test that fails without the fix.

## Coding standards

- Match the style, naming, and comment density of the surrounding code.
- Keep the public API typed — the package ships a `py.typed` marker and `.pyi`
  stubs; update stubs when you change public signatures.
- Prefer small, focused pull requests with a clear description of the problem
  and the fix.

## Pull request process

1. Fork and create a topic branch off `main`.
2. Make your change with tests and passing checks.
3. Sign off every commit (see DCO below).
4. Open a PR describing the change and linking any related issue.

## Developer Certificate of Origin (DCO)

We require the [Developer Certificate of Origin](https://developercertificate.org/)
on all contributions. It is a lightweight, per-commit affirmation that you wrote
the change (or otherwise have the right to submit it) under the project's MIT
license. There is no separate CLA to sign.

Add a sign-off line to each commit:

```
Signed-off-by: Your Name <your.email@example.com>
```

The easy way is to pass `-s` (or `--signoff`) when you commit:

```bash
git commit -s -m "Fix the thing"
```

The name and email must match the commit author. To sign off the most recent
commit you forgot to sign:

```bash
git commit --amend -s --no-edit
```

For a branch of unsigned commits, rebase with sign-off:

```bash
git rebase --signoff main
```

Pull requests whose commits are not signed off will be asked to add the
sign-off before they can be merged.

### The DCO text

By signing off, you certify the following:

> By making a contribution to this project, I certify that:
>
> (a) The contribution was created in whole or in part by me and I have the
>     right to submit it under the open source license indicated in the file; or
>
> (b) The contribution is based upon previous work that, to the best of my
>     knowledge, is covered under an appropriate open source license and I have
>     the right under that license to submit that work with modifications,
>     whether created in whole or in part by me, under the same open source
>     license (unless I am permitted to submit under a different license), as
>     indicated in the file; or
>
> (c) The contribution was provided directly to me by some other person who
>     certified (a), (b) or (c) and I have not modified it.
>
> (d) I understand and agree that this project and the contribution are public
>     and that a record of the contribution (including all personal information
>     I submit with it, including my sign-off) is maintained indefinitely and
>     may be redistributed consistent with this project or the open source
>     license(s) involved.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
