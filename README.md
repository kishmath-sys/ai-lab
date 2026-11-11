# AI Lab

AI Lab passes one request through ChatGPT, Claude and Gemini in a set order and produces a Word document and an interactive web page. It scans the results for accessibility problems and can email the finished files to you.

It uses the official command line tools for each service and your own existing paid subscriptions. There are no API keys and no per-use fees from this program.

Built by an educator for course materials. Shared as is.

## What it does

Default order of steps. You can change the order, the AI for each step and the job of each step in the window.

1. Gemini researches, using live search
2. ChatGPT checks and extends the research
3. Claude builds the Word document and web page
4. The program scans the files for accessibility problems (automatic, uses no AI)
5. ChatGPT reviews accuracy and accessibility
6. Claude applies fixes, then the program scans again

Other features
- Built-in step orders plus your own saved orders, and custom review steps
- Resume from where a run stopped
- Usage estimate report for each run and for the last 5 hours, 24 hours and 7 days
- Gemini model chooser
- Optional review of your request by a local AI (Ollama or LM Studio) that runs only on your computer. The button appears only when one is found
- Optional email of the finished files to your own Gmail
- Reads PDF, Word, txt, md, html, csv and json input, with drag and drop onto the files box

## Requirements

- Windows 11
- Python 3 with tkinter (the installer checks for this)
- Paid subscriptions you already have to ChatGPT, Claude and Google AI, each with its command line tool signed in
  - Claude Code (`claude`)
  - Codex CLI (`codex`)
  - Antigravity CLI (`agy`) for Gemini
- Optional: a Gmail address with 2-Step Verification and an app password, for the email feature

You must use your own accounts and follow each provider's terms of service. Subscription limits still apply, and long runs can use a lot of your allowance. Watch your usage.

## Install

1. Build the installer: `python build.py dist`
2. Run it in PowerShell: `powershell -ExecutionPolicy Bypass -File "dist\Install-AILab.ps1"`
3. Follow the prompts. The program installs to `C:\ai-lab` and adds a desktop icon.

Read `HOW-TO.txt` for everyday use.

## Accessibility scan limits

The scanner screens for common WCAG 2.1 Level A and AA problems. It is not an ADA or WCAG conformance test and it is not legal advice.

- It reads the code and does not render the pages
- It cannot judge reading order, wording quality or screen reader behavior
- Contrast is calculated only from colors written in the CSS

Always test final files with a keyboard and a screen reader, and check your institution's accessibility requirements.

## Privacy and safety

- Your email address and Gmail app password are stored in plain text in `config.json` on your own computer. Never commit or share that file. The included `.gitignore` excludes it.
- Run folders (`runs\`) hold your source material and AI output. Do not commit them.
- Your request text and attached files are sent to ChatGPT, Claude and Gemini through their tools. Do not use material you are not allowed to share with those services, and never include student records.
- AI output can be wrong. Review everything before you use it.

## Status

- Tested on two Windows 11 computers
- The AI command line tools change often, so parts may need updates
- No warranty and no promised support. Issues and pull requests may not get a reply

## Files

- `ailab.py` runs the steps and calls the AI tools
- `launcher.pyw` is the window
- `convert.py` reads and writes Word files and builds the web page
- `a11y.py` is the accessibility scanner and can be used on its own
- `mailer.py` sends the Gmail message
- `localai.py` finds and talks to a local AI on this computer, using localhost only
- `build.py` and `installer_template.ps1` build the single-file installer

## License

MIT. See `LICENSE`.
