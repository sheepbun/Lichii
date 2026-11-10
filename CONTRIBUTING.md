# Contributing

Bunx2 is developed in [sheepbun/Bunx2](https://github.com/sheepbun/Bunx2), a fork of
[zortos293/Lichii](https://github.com/zortos293/Lichii), which is in turn a fork of
[Djihads80/Eglantine](https://github.com/Djihads80/Eglantine) (now hosted as
[Eglantine-Community/Eglantine](https://github.com/Eglantine-Community/Eglantine)).

## Where contributions go

Every contribution from this fork is pushed to sheepbun/Bunx2, with no exceptions. Push branches
only to `origin` (`https://github.com/sheepbun/Bunx2.git`), and open pull requests and issues only
on sheepbun/Bunx2 (`gh pr create -R sheepbun/Bunx2`). The `zortos` and `upstream` remotes are for
fetching and reading only, including when you port a fix from them.

Before writing a fix, and again before opening its pull request, check the pull requests, issues
and branches of zortos293/Lichii and Djihads80/Eglantine for an existing solution. Port it if one
exists, citing its repository and PR or commit. See "Before writing a fix" in
[AGENTS.md](AGENTS.md).

Set up a new clone with these remotes and guards:

```sh
git clone https://github.com/sheepbun/Bunx2.git
cd Bunx2
git remote add zortos https://github.com/zortos293/Lichii.git
git remote add upstream https://github.com/Djihads80/Eglantine.git
git remote set-url --push zortos push-disabled-use-origin
git remote set-url --push upstream push-disabled-use-origin
git config remote.pushDefault origin
cp hooks/pre-push "$(git rev-parse --git-common-dir)/hooks/pre-push"
gh repo set-default sheepbun/Bunx2
```

`hooks/pre-push` rejects a push to any repository other than sheepbun/Bunx2, over HTTPS or SSH.
Do not bypass it with `--no-verify`. If an older copy of the hook that only allows sheepbun/Lichii
is installed, copy the current one over it.

## Sharing improvements upstream

Improvements made here are welcome upstream whenever Lichii's or Eglantine's maintainers find them
useful. Keep each change focused, explain the problem and the evidence in its commit message, and
avoid mixing Bunx2 branding or experiments into a fix, so it can be cherry-picked or adapted.
Proposing a change upstream is a decision for its author and is subject to that project's review
and licensing; nothing here is submitted upstream automatically.

## Building and testing

Bunx2 is built, run and tested on one machine: Arch Linux x86_64, an Intel Core i7-11700 and an
NVIDIA GeForce RTX 3080. See the [build guide](docs/Build.md) and the
[coding guidelines](docs/Coding.md). Keep changes focused, describe what you tested, and
distinguish build checks from gameplay testing.

Original copyright notices and third-party licenses remain in effect. See
[LICENSE.txt](LICENSE.txt) for the project's license.
