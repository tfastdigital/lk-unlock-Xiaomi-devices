# Contributing to lk-unlock

Thanks for taking an interest. This is a small, focused tool, so the rules are simple.

## Reporting a bug

Open an issue and include:

- The command you ran
- The full output (trim any token or key material first — never paste `private.pem`, `public.pem`, or `signature.bin`)
- Your Python version and OS
- Whether `fastboot` is reachable from your terminal

## Suggesting a feature

Open an issue describing the idea and why it helps. Keep it to one idea per issue so it's easy to discuss.

## Pull requests

- Keep changes small and focused on a single concern.
- Follow the existing style: typed functions, a docstring on new helpers, and the same logging style.
- Make sure every `.py` file you add starts with the copyright header:

  ```bash
  python scripts/license_header.py add
  ```

- Run the checks before you open a PR:

  ```bash
  task check-license
  task lint
  ```

## Code of conduct

Be respectful. This is a research/education project. We won't help with anything that harms others or their devices without consent.

## Attribution

If you build on this work, keep the AGPL notice and the TFast Digital Agency attribution in place. The `check-license` task exists exactly for that.
