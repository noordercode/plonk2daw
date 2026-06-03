# plonk2daw

**Spot audio from Soundminer into Pro Tools over the official Pro Tools Scripting (PTSL) API with intelligent track placement capabilities.**

`plonk2daw` watches for files Soundminer transfers and places them onto the Pro Tools timeline at your edit insertion: as **one multichannel clip** on the selected/matching track (creating a correctly-formatted track when needed), or **split across mono lanes** — decided by what you select in Pro Tools or force via hotkey.

> Independent, unofficial tool. Not affiliated with or endorsed by Avid or Soundminer.

## Why

Soundminer's native *Spot to DAW* on Windows places audio by driving the Pro Tools window. That works, but it (1) has no track intelligence by just dropping audio onto the selected track/streams, and
(2) currently mishandles session paths containing characters like `&`, so the spot silently fails while the file transfers fine.

`plonk2daw` places audio via Pro Tools' **PTSL `ImportAudioToClipList` → `SpotClipsByID`** API instead — robust, version-stable, indifferent to path punctuation, and adds the track logic:

- **poly** *(default; multichannel / SFX / HOA ambix / field recordings)* — one multichannel clip.
  Placed on the selected track if the file fits it; otherwise a new track of the file's exact format (Mono … 7.1.6, 1st–7th-order Ambisonics) is created at the selection.
- **spread** *(DX editting or split channels)* — the file's N channels go onto N mono lanes, reusing the lanes you selected and creating any extras.
- **auto** — poly, unless you've selected ≥2 tracks → spread.

## How it fits the workflow

Select a track + cursor in Pro Tools → switch to Soundminer → spot. Soundminer does the smart
*transfer* (transcode, metadata, format/channel layout); `plonk2daw` does the smart *placement*. This also works if there is no track selected at the last timeline position.

## How it works

Two halves with no glue between them: **Soundminer transfers, `plonk2daw` places.**

**Start it once and leave it running:**
```
watchdog.bat
```
While idle it touches nothing — ~0 % CPU and no Pro Tools connection — it only wakes to place a clip when one actually arrives.

**It finds your session by itself by checking the %APPDATA%\Roaming\Soundminer\SoundminerLog.txt for the transfer path; there's nothing to point it at.** Every time Soundminer transfers a file it writes a line to its log:
```
Generated FilePath: …\<your session>\Audio Files\<sound>.wav
```
`plonk2daw` tails that log and spots *exactly that file*. So the target always follows whatever session you're working in — switch Pro Tools sessions and Soundminer's transfer path moves with you, and `plonk2daw` follows along automatically. No per-session setup, no path to configure. (It only acts on files inside an `Audio Files` folder, so ordinary batch transfers elsewhere are ignored.) Idiot-proof by design.

**The loop, from your chair:**
1. In **Pro Tools**, click the destination track and park the cursor (or make a range / select several tracks — see the modes above).
2. **Switch to Soundminer**, find the sound, and press **Bring Into DAW**. This is a *Soundminer* hotkey — it only fires while Soundminer is focused, and it transfers the file into the session **without** placing it on the timeline, leaving the placement to `plonk2daw`.
3. `plonk2daw` sees the file land, reads Pro Tools' live cursor + selection, and drops the clip there.

You never break the Soundminer → Pro Tools rhythm you already have; `plonk2daw` just does the placing in the background.

**Force-spread on the fly.** Placement normally follows your selection (1 track → one clip; ≥2 tracks → spread). To force *spread* regardless — e.g. to split a multichannel file onto fresh mono lanes from a single selection — tap **`Alt+Shift+S`**. It's a sticky toggle (tap again for auto), and like the Soundminer key it only acts while **Soundminer is focused** — flip it right before you spot, and it won't toggle by accident while you're off in another app; the watcher prints the active mode each time.



## Requirements

- **Pro Tools 2026.x** (last tested version) with Scripting (PTSL) enabled (server on `localhost:31416`).
- **Avid Pro Tools Scripting SDK** — for `PTSL.proto`. *Not shipped here; obtain it under Avid's license with a developer account and generate the stubs (below).*
- **Python 3.10+**, Windows (the watcher's hotkey uses Win32).
- **Soundminer** (for the transfer half).

## Setup

```
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python generate_stubs.py --proto "C:\path\to\PTSL_SDK_CPP...\Source\PTSL.proto"
```

`generate_stubs.py` writes `PTSL_pb2.py` and `PTSL_pb2_grpc.py` next to the scripts (gitignored).

## Usage

Seamless watcher:
```
python watch.py
```
- `Alt+Shift+S` — sticky toggle to force *spread*.

One-shot / manual:
```
python spotter.py --probe                      # check the PT connection; read cursor + selected track
python spotter.py --file "snd.wav"             # --mode auto|poly|spread ; --ambi for ambisonic new tracks
```

## Notes

- Pro Tools reads a bare multichannel WAV (e.g. B-Format) as `Quad` — the file carries no ambisonic flag. `plonk2daw` decides by **channel count and honors the track you select**, so pick your
  Ambisonics track and it lands there correctly.
- PTSL header version defaults to `2026/4`; pass `--ptsl-version 1` if a host reports a version mismatch.

## Author & License

By **Stan van den Baar** (Noordersound). Licensed under the **MIT License** — see [LICENSE](LICENSE). © 2026 Stan van den Baar.

Avid's PTSL SDK remains Avid's and must be obtained under their license; this project ships none of it.
