<div align="center">

# JARVIS

### The AI assistant that never leaves your laptop.

Say **“Hey Jarvis”**, ask a question, get a spoken answer — with no account, no API key,
no subscription, and nothing sent to anyone's server. Ever.

<br/>

<!-- These are DIRECT downloads that never need touching again, and both halves
     of that matter:
       * /releases/latest/download/<name> — GitHub resolves this to the newest
         release at click time, so no tag appears here and nothing 404s when the
         next one ships. A tag-pinned link (v0.1.0-rc6/Jarvis_…dmg) breaks the
         day after it is written, on the first page a stranger sees.
       * the file names carry NO version, which is what makes the above possible
         — the URL needs an exact asset name. release.yml publishes these stable
         copies alongside the versioned ones, and SHA256SUMS.txt covers both.
     Two things will silently break this: renaming an asset, or marking a
     release as a pre-release (/latest skips those). Enforced as far as a
     grep can by the "README download links" gate in .github/workflows/ci.yml. -->

**[⬇︎ macOS](https://github.com/AayushSharma1003/jarvis/releases/latest/download/Jarvis-macOS-AppleSilicon.dmg)** &nbsp;·&nbsp;
**[⬇︎ Windows](https://github.com/AayushSharma1003/jarvis/releases/latest/download/Jarvis-Windows-x64-setup.exe)** &nbsp;·&nbsp;
**[⬇︎ Linux](https://github.com/AayushSharma1003/jarvis/releases/latest/download/Jarvis-Linux-x86_64.deb)**

<sub>macOS 12+ (Apple Silicon) · Windows 10/11 (64-bit) · Ubuntu/Debian (64-bit)<br/>
Free and open source · [all downloads &amp; checksums](https://github.com/AayushSharma1003/jarvis/releases/latest)</sub>

<br/>

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/macOS%20%C2%B7%20Windows%20%C2%B7%20Linux-lightgrey)
![Works offline](https://img.shields.io/badge/works-offline-brightgreen)
![Telemetry](https://img.shields.io/badge/telemetry-none-brightgreen)

</div>

<!-- DEMO GIF: "Hey Jarvis" → question → spoken answer, sphere reacting. -->

---

## What it is

Most AI assistants are a microphone pointed at someone else's data centre. Jarvis is the
opposite: the model, your conversations and your voice all stay on your machine. Pull out
the network cable and it still works.

You talk to it, and it talks back in about a second and a half, while a glass sphere
pulses along with the conversation. It can also *do* things — read and write files, run
commands, fetch a web page. Every one of those asks you first.

> **Jarvis is young.** It's fast and genuinely useful, but this is an early release and
> you will find rough edges. What it will never do is phone home about them.

---

## What you can do with it

| | |
|---|---|
| 🎙️ **Talk, hands-free** | Say “Hey Jarvis” from across the room. It listens locally for about 2% of one CPU core — and you can cut it off mid-sentence, like you would a person. |
| ⌨️ **Or just type** | A proper chat app: streaming replies, conversation history, rename and delete, and a model picker that knows what your RAM can actually handle. |
| 📁 **Work with your files** † | *“Summarise the notes in my Documents folder.”* Reading is free, writing asks, deleting asks loudly — and only inside folders you've allowed. |
| 💻 **Run commands** † | *“What's using port 3000?”* The full command is shown and runs only after you click Allow. No exceptions. |
| 🌐 **Read the web** † | Fetch a page and ask about it, behind a guard that stops a malicious link turning Jarvis into a probe of your home network. |
| 🌳 **Rewind and branch** | Edit any question or retry any answer. The old one isn't overwritten — it becomes a branch you flip back to with `‹ 2/3 ›`. |
| ⏰ **Timers and reminders** | *“Remind me in 20 minutes.”* It says so out loud when the time comes, and survives a restart. |
| 🧩 **Extend it** | Extensions are plain Python, declare what they need, and load only after you approve them. |

<sub>† These three need a model Jarvis has measured as able to *decline* a tool — today
that's `qwen3:4b`. With the faster `llama3.2:3b` they're switched off, and Jarvis says so
above the message box. See “Which model should I pull?” below.</sub>

---

## Get started in 5 minutes

Jarvis runs a language model on your own computer, so there are two pieces: **Ollama**,
which runs the model, and **Jarvis**, which is everything else.

### 1. Install Ollama, open it, and pull a model

Jarvis deliberately doesn't bundle Ollama — it's a separate tool you may already use, and
quietly installing a background service on your machine isn't our call to make.

Get it from **[ollama.com/download](https://ollama.com/download)**, then **open it once**.
That's not a formality: opening it is what installs the `ollama` command and starts the
local server, and without it the next line just says `command not found`.

```bash
ollama pull llama3.2:3b
```

<sub>~2 GB. **Leave Ollama running while you use Jarvis** — it's the thing doing the
thinking. On macOS it lives in the menu bar and starts with your Mac; if you quit it,
Jarvis will say it can't reach Ollama.</sub>

> [!IMPORTANT]
> **That model talks fast, but it can't touch your files.** Reading files, running
> commands and fetching web pages are **off** for `llama3.2:3b`. They need a model
> Jarvis has measured as able to *decline* a tool, and today that is `qwen3:4b`:
>
> ```bash
> ollama pull qwen3:4b     # +2.5 GB — files, commands and web, but ~20s before it speaks
> ```
>
> Install both and switch in the app whenever you like. Jarvis says which mode
> you're in above the message box.

<details>
<summary><b>Why is it like that, and which should I actually use?</b></summary>

<br/>

| | `llama3.2:3b` | `qwen3:4b` |
|---|---|---|
| **Voice** | ~1.4 s to a spoken reply | ~20 s before it starts talking |
| **Files, commands, web** | ❌ off | ✅ on |
| **Size** | 2.0 GB | 2.5 GB |

Jarvis only hands tools to models it has actually measured refusing them, and
`llama3.2:3b` fails that test badly — asked *“what's 17 times 4?”* it answers by running a
shell command. A model that reaches for tools it doesn't need buries you in permission
prompts until you stop reading them, which is a security problem rather than an annoying
one. So tools stay off for it, and there is no override.

`qwen3:4b` is the only model that currently ships with tools on. It's genuinely better at
them — and it thinks before it answers, which voice can't afford.

**So:** `llama3.2:3b` if you mostly want to talk to it, `qwen3:4b` if you want it to do
things on your computer. Most people should pull both.

**More than 8 GB of RAM?** Pull something bigger (`ollama pull qwen2.5:7b` at 16 GB, `14b`
at 32 GB). Jarvis picks the best of what you have *installed* — it can't suggest a model
you never downloaded — and it shows the ceiling your RAM allows in the empty chat.

</details>

### 2. Install Jarvis

Download it from the links at the top. Because this is a free project without a $99/year
Apple certificate or a several-hundred-dollar Windows one, **your OS will warn you the
first time you open it.** Nothing is wrong — here's exactly what you'll see.

<details>
<summary><b>macOS</b> — “Apple could not verify…”</summary>

<br/>

1. Open the `.dmg` and **drag Jarvis onto the Applications folder.** Don't run it from
   inside the disk image — macOS runs apps from there in a read-only sandbox and Jarvis
   will misbehave.
2. Open it from Applications. A dialog says Apple can't verify it. Click **Done** —
   *not* “Move to Bin”.
3. Go to **System Settings → Privacy &amp; Security**, scroll down, and click **Open Anyway**.
4. Launch it once more and confirm. You won't be asked again.
5. **Allow the microphone** when prompted — the wake word and voice need it.

> Upgrading from an older Jarvis? macOS will ask for the microphone again. That's
> expected: the app's signature changed, and macOS ties permissions to it.

</details>

<details>
<summary><b>Windows</b> — “Windows protected your PC”</summary>

<br/>

1. Run the installer. SmartScreen shows a blue box.
2. Click **More info**, then **Run anyway**.
3. Allow microphone access when Windows asks.

</details>

<details>
<summary><b>Linux</b> — Debian / Ubuntu</summary>

<br/>

```bash
sudo apt install ./Jarvis-Linux-x86_64.deb
```

Then launch Jarvis from your applications menu.

</details>

<sub>Every download is checksummed — verify against `SHA256SUMS.txt` on the
[releases page](https://github.com/AayushSharma1003/jarvis/releases) with
`shasum -a 256 --ignore-missing -c SHA256SUMS.txt` on macOS/Linux, or `Get-FileHash` on
Windows. <!-- --ignore-missing matters: SHA256SUMS lists every artifact, so without it
someone who downloaded one file sees six lines of FAILED and one OK. -->
</sub>

### 3. Turn on voice

Open Jarvis. If voice isn't ready, it says so and shows a **Download voice models**
button — click it. That's about 500 MB of speech-recognition and text-to-speech models,
downloaded once and kept on your machine. Nothing downloads until you press it.

Then click the microphone, or just say **“Hey Jarvis.”**

---

## What it needs

| | Minimum | Comfortable |
|---|---|---|
| **RAM** | 8 GB | 16 GB or more |
| **Disk** | ~3 GB — 0.2 app + 0.5 voice models + ~2 for the language model | more if you keep several models |
| **macOS** | 12 Monterey, **Apple Silicon only** | M1 or newer |
| **Windows** | 10 or 11, 64-bit | |
| **Linux** | Debian/Ubuntu-based, 64-bit | |

Jarvis picks a model that fits your machine — **8 GB → ~3B parameters**, **16 GB → ~8B**,
**32 GB → ~14B**, larger above that — and you can always override it.

> **Intel Macs aren't supported yet.** The macOS build is Apple Silicon only.

---

## How fast is it?

Measured on the slowest machine it targets — an **8 GB M2 MacBook** running `llama3.2:3b`:

| | |
|---|---|
| You stop speaking → Jarvis starts speaking | **1.2–1.4 seconds** |
| First word of a typed reply | **407 ms** |
| Always-on wake word, idle CPU cost | **2.4% of one core** |

A faster machine or a bigger model moves the first two. The wake word cost stays flat.

---

## Your privacy, concretely

The part most assistants are vague about, in specifics:

- **No account, no sign-in, no API key.** There is nothing to log into.
- **No telemetry, no analytics, no crash reporting.** Not “anonymised” — absent.
- **No auto-update.** Jarvis never contacts a server to see if it's out of date.
- **Your conversations** live in a local SQLite file you can read, back up or delete.
- **Your voice** is transcribed on your machine, and the audio is thrown away.
- **The only network traffic** is what you ask for: fetching a web page, talking to Ollama
  on your own machine, or downloading the voice models when you press the button.

Turn off your Wi-Fi and Jarvis keeps working.

---

## When Jarvis does things on your computer

An assistant that can run commands and delete files is only as good as the moment it stops
and asks. That system was designed before the tools existed, not bolted on after:

- **Every action is rated** *routine*, *needs permission*, or *risky* — and the rating is
  decided by Jarvis itself, never by the AI model.
- **Shell commands always ask**, showing the exact command. There's no clever
  “safe command” detector, because those are eventually wrong and always confident.
- **Files are fenced.** Jarvis reaches only folders you've allowed, and its own config and
  data are off-limits to it.
- **Untrusted content raises the bar.** Once a web page or unknown file enters a
  conversation, anything with a side-effect asks again and tells you *why*. A web page
  cannot talk Jarvis into deleting your files.
- **“Allow this session”** covers that exact action with those exact arguments, and is
  never offered for risky ones.

**One thing to be clear about:** an extension you approve runs with Jarvis's full
privileges. Its declared permissions tell you its intent — they are not a cage. Approve
extensions the way you'd approve any program.

---

## Known limits

Said plainly, because finding out later is worse:

- **Hands-on tested on macOS (Apple Silicon).** Windows and Linux are built automatically
  for every release but haven't yet had hands-on testing. If you're on one, you're early —
  please [tell us what breaks](https://github.com/AayushSharma1003/jarvis/issues).
- **No Intel Mac build** yet.
- **Voice is English**, and the wake word is “Hey Jarvis” — it can't be changed yet.
- **No settings screen.** Configuration is a TOML file in Jarvis's data folder.
- **Ollama is required**, installed separately.
- **No echo cancellation.** With loud speakers, Jarvis can hear itself.

---

## Built with

Tauri 2, React 19 and three.js on the front; a Python sidecar behind it running
[whisper.cpp](https://github.com/ggerganov/whisper.cpp) for speech recognition,
[Silero VAD](https://github.com/snakers4/silero-vad) for knowing when you've stopped
talking, [openWakeWord](https://github.com/dscripka/openWakeWord) for the wake word,
[Kokoro](https://github.com/thewh1teagle/kokoro-onnx) for the voice, and
[Ollama](https://ollama.com) for the language model. One ML runtime, no PyTorch, no 2 GB
surprise in your Applications folder.

Curious how it fits together? See [architecture.md](docs/architecture.md), the
[security model](docs/security-model.md), and [where the 1.4 seconds go](docs/latency.md).

## Build from source

```bash
git clone https://github.com/AayushSharma1003/jarvis.git && cd jarvis
cd backend && uv sync && uv run python ../scripts/fetch_models.py
cd ../app && npm install && npm run tauri dev
```

Requires [uv](https://docs.astral.sh/uv/), Node 22+ and a Rust toolchain.

## Contributing

Issues and pull requests are welcome — especially **Windows and Linux bug reports**, which
are the least-tested part of the project. Include your OS, your RAM and what you asked
Jarvis to do. See [CONTRIBUTING.md](CONTRIBUTING.md); anything touching
`backend/jarvis_backend/security/` wants an issue first.

## License

[Apache-2.0](LICENSE). No model weights live in this repository — they're downloaded from
upstream when you ask for them. Third-party components are credited in [NOTICE](NOTICE).

**One licensing caveat worth knowing:** openWakeWord's pre-trained wake-word models are
**CC BY-NC-SA 4.0 (non-commercial)**. That constraint belongs to those downloaded weights,
not to Jarvis itself — but if you plan to use Jarvis commercially, the wake word is the
piece to look at.

---

<div align="center">
<sub>Built by <a href="https://github.com/AayushSharma1003">Aayush Sharma</a></sub>
</div>
