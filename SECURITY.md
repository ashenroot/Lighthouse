# Security

Lighthouse is meant to be run by people who are not security engineers, so the defaults are conservative: the relay only reads public feeds, push tokens are stored hashed, secrets never reach the board, and pushed text is shown as plain text.

## Reporting a problem

Please report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on this repository. Do not open a public issue for something exploitable. You will get an answer within a few days.

Useful to include: the version (`fresnel --version`), how you run it, and the steps that show the problem.

## What is in scope

- The relay (`fresnel/`): authentication of pushes, token handling, injection, denial of service from untrusted input, leaking of configuration or location.
- The board (`board/`): script injection from pushed or fetched content, anything that weakens the content security policy.
- The installer and update command.

Feeds Lighthouse reads belong to their publishers; report problems with those to them.
