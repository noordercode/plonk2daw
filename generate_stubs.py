#!/usr/bin/env python3
"""
Generate the gRPC/protobuf Python stubs plonk2daw needs (PTSL_pb2.py,
PTSL_pb2_grpc.py) from Avid's Pro Tools Scripting SDK proto (PTSL.proto).

The proto is part of Avid's PTSL SDK (licensed) and is NOT shipped with
plonk2daw -- supply your own copy from the SDK's Source/ folder.

Usage:
    python generate_stubs.py --proto "C:\\path\\to\\PTSL_SDK_CPP...\\Source\\PTSL.proto"
    python generate_stubs.py            # looks for PTSL.proto next to this script

Requires grpcio-tools (see requirements.txt).
"""
import argparse, os, sys, subprocess

def main():
    ap = argparse.ArgumentParser(description="Build PTSL_pb2*.py from Avid's PTSL.proto")
    ap.add_argument("--proto", default="PTSL.proto", help="path to Avid's PTSL.proto")
    args = ap.parse_args()

    proto = os.path.abspath(args.proto)
    if not os.path.isfile(proto):
        print("ERROR: PTSL.proto not found at:\n  %s" % proto)
        print("Point --proto at your Avid PTSL SDK's Source\\PTSL.proto.")
        return 1

    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, "-m", "grpc_tools.protoc",
           "-I", os.path.dirname(proto),
           "--python_out", here, "--grpc_python_out", here,
           os.path.basename(proto)]
    print("Generating stubs:\n  " + " ".join('"%s"' % c if " " in c else c for c in cmd))
    try:
        rc = subprocess.run(cmd).returncode
    except FileNotFoundError:
        print("ERROR: grpc_tools not available. Install deps first:  pip install -r requirements.txt")
        return 1
    if rc == 0:
        print("OK -> PTSL_pb2.py and PTSL_pb2_grpc.py written to:\n  %s" % here)
    else:
        print("protoc failed (exit %d)." % rc)
    return rc

if __name__ == "__main__":
    sys.exit(main())
