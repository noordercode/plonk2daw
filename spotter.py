"""
plonk2daw -- place Soundminer-transferred audio into Pro Tools 2026.4 via the PTSL API (core engine).

Places a (Soundminer-transcoded) WAV onto the Pro Tools timeline by talking to
PT's PTSL gRPC server on localhost:31416 directly -- bypassing Soundminer's
broken Windows-UI-automation spot path.

Placement modes (--mode):
  poly   : one (multichannel) clip on the selected track if its format matches the
           file; otherwise auto-create a new track of the file's exact format
           (Mono..7.1.6, 1st-7th-order Ambisonics) at the selection and place it there.
           For SFX / HOA ambix / multichannel FX / field recordings.   [default]
  spread : split the file's N channels across N new mono tracks at the selection,
           one channel per track. For DX editing.

Range fill: if a timeline RANGE is selected (out > in), the placed clip(s) are
trimmed to that range after spotting (poly and spread). A zero-length cursor =
normal placement. Trim only shortens to fit; a clip shorter than the range is
left as placed.  --no-fill disables it.

Run from inside this folder (so the generated PTSL_pb2*.py import flatly):
    python spotter.py --probe
    python spotter.py --file "D:\\...\\Audio Files\\snd.wav" --mode poly
    python spotter.py --file "...wav" --mode spread
"""
__author__ = "Stan van den Baar"
__license__ = "MIT"

import sys, os, json, argparse, grpc
import PTSL_pb2 as pb
import PTSL_pb2_grpc as pbg

SERVER = "localhost:31416"
VERSION = (2026, 4, 0)            # PTSL_Versions.h (year, month, revision)
COMPANY, APP = "Noordersound", "plonk2daw"

CID = pb.CommandId
TS = pb.TaskStatus
def _v(enum, name): return enum.Value(name)
def _has(enum, name):
    try:
        enum.Value(name); return True
    except (ValueError, KeyError):
        return False
TERMINAL = {_v(TS, n) for n in ("TStatus_Completed", "TStatus_Failed",
            "TStatus_CompletedWithBadResponse", "TStatus_FailedWithBadErrorResponse")}
COMPLETED = _v(TS, "TStatus_Completed")

def stem_to_track_format(stem_name):
    """'SFormat_1stOrderAmbisonics' -> 'TFormat_1stOrderAmbisonics' if PT has that track format.
    Returns None for SFormat_None/unknown -- 'TFormat_None' is a sentinel, not a creatable track format."""
    if not stem_name or not stem_name.startswith("SFormat_") or stem_name == "SFormat_None":
        return None
    cand = "TFormat_" + stem_name[len("SFormat_"):]
    return cand if (_has(pb.TrackFormat, cand) and cand != "TFormat_None") else None

# channels per track format (for "does the file fit the selected track?")
TRACK_CHANNELS = {
    "TFormat_Mono": 1, "TFormat_Stereo": 2, "TFormat_LCR": 3, "TFormat_LCRS": 4, "TFormat_Quad": 4,
    "TFormat_5_0": 5, "TFormat_5_1": 6, "TFormat_5_0_2": 7, "TFormat_5_1_2": 8, "TFormat_5_0_4": 9, "TFormat_5_1_4": 10,
    "TFormat_6_0": 6, "TFormat_6_1": 7, "TFormat_7_0": 7, "TFormat_7_1": 8, "TFormat_7_0_SDDS": 7, "TFormat_7_1_SDDS": 8,
    "TFormat_7_0_2": 9, "TFormat_7_1_2": 10, "TFormat_7_0_4": 11, "TFormat_7_1_4": 12, "TFormat_7_0_6": 13, "TFormat_7_1_6": 14,
    "TFormat_9_0_4": 13, "TFormat_9_1_4": 14, "TFormat_9_0_6": 15, "TFormat_9_1_6": 16,
    "TFormat_1stOrderAmbisonics": 4, "TFormat_2ndOrderAmbisonics": 9, "TFormat_3rdOrderAmbisonics": 16,
    "TFormat_4thOrderAmbisonics": 25, "TFormat_5thOrderAmbisonics": 36, "TFormat_6thOrderAmbisonics": 49, "TFormat_7thOrderAmbisonics": 64,
}
# ambisonic track format by channel count ((order+1)^2), for --ambi new-track creation
AMBI_BY_CH = {4: "TFormat_1stOrderAmbisonics", 9: "TFormat_2ndOrderAmbisonics", 16: "TFormat_3rdOrderAmbisonics",
              25: "TFormat_4thOrderAmbisonics", 36: "TFormat_5thOrderAmbisonics", 49: "TFormat_6thOrderAmbisonics",
              64: "TFormat_7thOrderAmbisonics"}

# channel count -> a standard, valid POLY track format, for files PT can't name
# (stem=SFormat_None: a 6ch field rec, the CESSNA A1..A6, etc.). Exact-count formats,
# so the "!= nch" guard in _do_poly passes and we place poly instead of spreading.
CHAN_TO_TFORMAT = {1: "TFormat_Mono", 2: "TFormat_Stereo", 3: "TFormat_LCR", 4: "TFormat_Quad",
                   5: "TFormat_5_0", 6: "TFormat_5_1", 7: "TFormat_7_0", 8: "TFormat_7_1",
                   9: "TFormat_5_0_4", 10: "TFormat_7_1_2", 11: "TFormat_7_0_4", 12: "TFormat_7_1_4",
                   13: "TFormat_7_0_6", 14: "TFormat_7_1_6", 15: "TFormat_9_0_6", 16: "TFormat_9_1_6"}


class PtslError(Exception):
    pass


class PtslClient:
    def __init__(self, server=SERVER, version=VERSION, verbose=True):
        self.channel = grpc.insecure_channel(server)
        self.stub = pbg.PTSLStub(self.channel)
        self.session_id = ""
        self.version = version
        self.verbose = verbose

    def _send(self, command_id, body):
        hdr = pb.RequestHeader(command=command_id, version=self.version[0],
                               version_minor=self.version[1], version_revision=self.version[2],
                               session_id=self.session_id)
        req = pb.Request(header=hdr, request_body_json=json.dumps(body or {}))
        body_json = error_json = ""
        status = None
        for resp in self.stub.SendGrpcStreamingRequest(req):
            status = resp.header.status
            if resp.response_body_json:
                body_json = resp.response_body_json
            if resp.response_error_json:
                error_json = resp.response_error_json
            if status in TERMINAL:
                break
        return status, body_json, error_json

    def call(self, command_id, body=None, name=""):
        try:
            status, body_json, error_json = self._send(command_id, body)
        except grpc.RpcError as e:
            raise PtslError("%s transport error: %s (is Pro Tools running on %s?)"
                            % (name, e.code(), SERVER))
        bd = json.loads(body_json) if body_json.strip() else {}
        ed = json.loads(error_json) if error_json.strip() else {}
        if self.verbose:
            sname = TS.Name(status) if status is not None else "NO_RESPONSE"
            tag = ""
            if ed:
                tag = ("  warn=" if status == COMPLETED else "  err=") + json.dumps(ed)
            print("  [%s] %s%s" % (sname, name or command_id, tag))
        if status != COMPLETED:
            raise PtslError("%s -> %s %s" % (name, TS.Name(status) if status is not None else "?", ed or error_json))
        return bd

    # --- connection / read ---
    def host_ready(self):
        return self.call(_v(CID, "CId_HostReadyCheck"), {}, "HostReadyCheck")

    def register(self, company=COMPANY, app=APP):
        bd = self.call(_v(CID, "CId_RegisterConnection"),
                       {"company_name": company, "application_name": app}, "RegisterConnection")
        self.session_id = bd.get("session_id", "")
        return self.session_id

    def get_session_path(self):
        return self.call(_v(CID, "CId_GetSessionPath"), {}, "GetSessionPath")

    def get_timeline_selection(self):
        return self.call(_v(CID, "CId_GetTimelineSelection"),
                         {"location_type": "TLType_Samples"}, "GetTimelineSelection")

    def get_selected_tracks(self):
        bd = self.call(_v(CID, "CId_GetTrackList"),
                       {"track_filter_list": [{"filter": "TLFilter_Selected"}],
                        "is_filter_list_additive": True,
                        "pagination_request": {"limit": 1000, "offset": 0}}, "GetTrackList")
        tracks = bd.get("track_list", [])
        tracks.sort(key=lambda t: t.get("index", 0))
        return tracks

    def get_selected_track(self):
        ts = self.get_selected_tracks()
        return ts[0] if ts else None

    def get_media_file_info(self, file_id):
        bd = self.call(_v(CID, "CId_GetMediaFileInfo"), {"file_id": file_id}, "GetMediaFileInfo")
        return bd.get("audio_file_info", {})

    # --- write ---
    def import_to_clip_list(self, wav):
        return self.call(_v(CID, "CId_ImportAudioToClipList"),
                         {"file_list": [wav], "audio_operations": "AOperations_AddAudio"},
                         "ImportAudioToClipList")

    def create_new_tracks(self, count, track_format, name="", after_track=None):
        body = {"number_of_tracks": count, "track_name": name, "track_format": track_format,
                "track_type": "TType_Audio", "track_timebase": "TTimebase_Samples"}
        if after_track:
            body["insertion_point_position"] = "TIPoint_After"
            body["insertion_point_track_name"] = after_track
        return self.call(_v(CID, "CId_CreateNewTracks"), body, "CreateNewTracks")

    def spot_clips(self, clip_ids, cursor, track_id=None, track_name=None):
        body = {"src_clips": list(clip_ids),
                "dst_location_data": {"location_type": "SLType_Start",
                                      "location": {"location": str(cursor), "time_type": "TLType_Samples"}}}
        if track_id:
            body["dst_track_id"] = track_id
        elif track_name:
            body["dst_track_name"] = track_name
        return self.call(_v(CID, "CId_SpotClipsByID"), body, "SpotClipsByID")

    # --- selection / trim (for fill-to-range) ---
    def select_tracks_by_name(self, names, mode="SMode_Replace"):
        # if a host rejects "SMode_Replace", the deprecated alias "SM_Replace" also works
        return self.call(_v(CID, "CId_SelectTracksByName"),
                         {"track_names": [n for n in names if n],
                          "selection_mode": mode}, "SelectTracksByName")

    def set_timeline_selection(self, in_time, out_time):
        return self.call(_v(CID, "CId_SetTimelineSelection"),
                         {"location_type": "TLType_Samples",
                          "in_time": str(in_time), "out_time": str(out_time)},
                         "SetTimelineSelection")

    def trim_to_selection(self):              # CId_TrimToSelection has NO request body
        return self.call(_v(CID, "CId_TrimToSelection"), {}, "TrimToSelection")

    def get_edit_mode_options(self):
        bd = self.call(_v(CID, "CId_GetEditModeOptions"), {}, "GetEditModeOptions")
        return bd.get("edit_mode_options", {})

    def set_edit_mode_options(self, opts):
        return self.call(_v(CID, "CId_SetEditModeOptions"),
                         {"edit_mode_options": opts}, "SetEditModeOptions")


def connect(verbose=True):
    c = PtslClient(verbose=verbose)
    c.host_ready()
    c.register()
    return c


def _clip_ids_and_file_id(imp):
    clip_ids, file_id = [], None
    for entry in imp.get("file_list", []):
        for f in entry.get("destination_file_list", []):
            clip_ids += f.get("clip_id_list", [])
            file_id = file_id or f.get("file_id")
    return clip_ids, file_id


def _has_range(in_time, out_time):
    """True if the timeline selection has length (a real range, not a bare cursor)."""
    try:
        return out_time is not None and int(out_time) > int(in_time)
    except (TypeError, ValueError):
        return False


def _fill_to_range(c, track_names, in_time, out_time):
    """Trim the just-spotted clip(s) on track_names to fit [in_time, out_time].
    Only SHORTENS clips overrunning the range; clips fully outside the range are
    untouched (PT's edit selection = selected tracks x time range). Best-effort:
    any failure here warns but leaves the completed placement intact."""
    names = [n for n in track_names if n]
    if not names:
        return
    # Trim consumes the EDIT selection; ensure timeline+edit selection are linked
    # (force it on if needed, restore the user's options afterwards).
    restore = None
    try:
        opts = c.get_edit_mode_options()
    except PtslError as e:
        print("  warn: GetEditModeOptions failed (%s); proceeding without link guard" % e)
        opts = {}
    if opts and not opts.get("link_timeline_and_edit_selection", False):
        restore = dict(opts)
        opts2 = dict(opts); opts2["link_timeline_and_edit_selection"] = True
        try:
            c.set_edit_mode_options(opts2)
        except PtslError as e:
            print("  warn: couldn't enable link_timeline_and_edit_selection (%s)" % e)
            restore = None
    try:
        c.select_tracks_by_name(names)
        c.set_timeline_selection(in_time, out_time)
        c.trim_to_selection()
        print("OK: trimmed to range [%s..%s] on %d track(s)" % (in_time, out_time, len(names)))
    except PtslError as e:
        print("  warn: fill/trim-to-range failed (%s); clip left full-length" % e)
    finally:
        if restore is not None:
            try: c.set_edit_mode_options(restore)
            except PtslError: pass


def spot_file(c, wav, mode="auto", ambi=False, no_fill=False):
    """mode: 'auto' (poly unless >=2 tracks selected), 'poly', or 'spread'.
    If a timeline RANGE is selected (out > in) and not no_fill, the placed
    clip(s) are trimmed to that range after spotting (poly and spread)."""
    base = os.path.splitext(os.path.basename(wav))[0]
    cursor = "0"
    out_time = play_time = None
    try:
        sel0 = c.get_timeline_selection()
        cursor = sel0.get("in_time") or sel0.get("play_start_marker_time") or "0"
        out_time = sel0.get("out_time")
        play_time = sel0.get("play_start_marker_time")
    except PtslError as e:
        print("  warn: cursor read failed (%s); using 0" % e)
    print("  selection: in=%s out=%s play=%s -> %s"
          % (cursor, out_time, play_time,
             "RANGE (will fill)" if (_has_range(cursor, out_time) and not no_fill) else "cursor only"))

    imp = c.import_to_clip_list(wav)
    clip_ids, file_id = _clip_ids_and_file_id(imp)
    if not clip_ids:
        raise PtslError("no clip ids returned: %s" % json.dumps(imp))

    info = c.get_media_file_info(file_id) if file_id else {}
    stem = info.get("stem_format")
    nch = info.get("num_channels") or len(clip_ids)
    selected = c.get_selected_tracks()
    print("  file: %d channels, stem=%s, clips=%d, cursor=%s, selected=%d track(s)"
          % (nch, stem, len(clip_ids), cursor, len(selected)))

    if mode == "auto":
        mode = "spread" if len(selected) >= 2 else "poly"
        print("  auto -> %s" % mode)

    if mode == "spread":
        targets = _do_spread(c, clip_ids, cursor, selected, base)
    else:
        targets = _do_poly(c, clip_ids, cursor, selected, nch, stem, ambi, base)

    if not no_fill and _has_range(cursor, out_time):
        _fill_to_range(c, targets, cursor, out_time)


def _do_poly(c, clip_ids, cursor, selected, nch, stem, ambi, base):
    # decide by CHANNEL COUNT and honour the selected track (your selection
    # disambiguates e.g. Quad vs B-Format -- PT reads a bare 4ch WAV as Quad).
    sel = selected[0] if selected else None
    sel_name = (sel or {}).get("name")
    sel_id = (sel or {}).get("id")
    sel_fmt = (sel or {}).get("format")
    sel_ch = TRACK_CHANNELS.get(sel_fmt, 0)
    if sel and sel_ch and nch <= sel_ch:
        note = "" if nch == sel_ch else "  (file %dch < track %dch)" % (nch, sel_ch)
        print("  fits selected track '%s' [%s, %dch]%s -> placing there" % (sel_name, sel_fmt, sel_ch, note))
        c.spot_clips(clip_ids, cursor, track_id=sel_id, track_name=sel_name)
        print("OK: clip on '%s' @ %s" % (sel_name, cursor))
        return [sel_name]
    else:
        if ambi:
            tf = AMBI_BY_CH.get(nch) or stem_to_track_format(stem)
        else:                                    # not forced: never invent ambi here
            tf = stem_to_track_format(stem)
            if tf in set(AMBI_BY_CH.values()):   # but not ambi -- that's only select-the-track or force
                tf = None
        if not tf:
            tf = CHAN_TO_TFORMAT.get(nch)
        if not tf or TRACK_CHANNELS.get(tf, 0) != nch:
            # unknown / unmappable multichannel layout (e.g. SFormat_None field rec):
            # no valid poly track format exists -> split to mono lanes instead of erroring.
            print("  no valid %dch poly format (stem=%s) -> spreading to mono lanes" % (nch, stem))
            return _do_spread(c, clip_ids, cursor, selected, base)
        why = "no usable track selected" if not sel_ch else ("file %dch > track %dch (%s)" % (nch, sel_ch, sel_fmt))
        print("  %s -> creating new %s track at selection" % (why, tf))
        created = c.create_new_tracks(1, tf, name=base, after_track=sel_name)
        ids = created.get("created_track_ids", [])
        names = created.get("created_track_names", [])
        tid = ids[0] if ids else None
        tname = names[0] if names else None
        c.spot_clips(clip_ids, cursor, track_id=tid, track_name=tname)
        print("OK: created %s track '%s', placed clip @ %s" % (tf, tname, cursor))
        return [tname]


def _do_spread(c, clip_ids, cursor, selected, base):
    # one channel per mono lane. Reuse selected lanes ONLY when you deliberately
    # selected 2+ tracks; with 0-1 selected, make all-new tracks (don't dump a
    # channel onto an incidental single selection). Never a poly remainder.
    n = len(clip_ids)
    reuse = selected[:n] if len(selected) >= 2 else []
    after = reuse[-1].get("name") if reuse else (selected[0].get("name") if selected else None)
    targets = [(t.get("id"), t.get("name")) for t in reuse]
    shortfall = n - len(targets)
    if shortfall > 0:
        created = c.create_new_tracks(shortfall, "TFormat_Mono", name=base, after_track=after)
        ids = created.get("created_track_ids", [])
        names = created.get("created_track_names", [])
        for i in range(shortfall):
            targets.append((ids[i] if i < len(ids) else None, names[i] if i < len(names) else None))
    for cid, (tid, tname) in zip(clip_ids, targets):
        c.spot_clips([cid], cursor, track_id=tid, track_name=tname)
    print("OK: spread %d channels (%d reused + %d new mono) @ %s"
          % (n, len(reuse), shortfall, cursor))
    return [tname for (_tid, tname) in targets]


def probe():
    print("Connecting to Pro Tools PTSL on %s ..." % SERVER)
    c = connect()
    print("session_id:", c.session_id)
    print("session path:", json.dumps(c.get_session_path()))
    print("timeline selection:", json.dumps(c.get_timeline_selection()))
    print("selected track:", json.dumps(c.get_selected_track()))
    print("PROBE OK -- our client talks to PT 2026.4.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="connect + read session/cursor/track, no placement")
    ap.add_argument("--file", help="WAV to spot")
    ap.add_argument("--mode", choices=["auto", "poly", "spread"], default="auto",
                    help="auto=poly unless >=2 tracks selected (default); poly=one (multichannel) clip; spread=split to N mono tracks (reusing selected lanes)")
    ap.add_argument("--ambi", action="store_true",
                    help="when creating a NEW track, treat the file as ambisonics (order by channel count) instead of PT's Quad/surround guess")
    ap.add_argument("--no-fill", action="store_true",
                    help="never trim placed clips to the timeline range (fill is automatic otherwise)")
    ap.add_argument("--ptsl-version", type=int, default=2026, help="header version (2026 default; try 1 on mismatch)")
    args = ap.parse_args()

    global VERSION
    if args.ptsl_version != 2026:
        VERSION = (args.ptsl_version, 0, 0)

    if args.probe:
        probe(); return 0
    if not args.file:
        ap.error("give --file <wav> or --probe")
    wav = os.path.abspath(args.file)
    if not os.path.isfile(wav):
        print("no such file:", wav); return 1
    c = connect()
    print("session:", c.get_session_path().get("session_path", {}).get("path", "?"))
    spot_file(c, wav, mode=args.mode, ambi=args.ambi, no_fill=args.no_fill)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PtslError as e:
        print("ERROR:", e)
        sys.exit(2)
