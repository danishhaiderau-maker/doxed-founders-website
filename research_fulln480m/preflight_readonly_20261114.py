"""P1 READ-ONLY PREFLIGHT for the BTC full-N 480,522,240 search (batch BP) - RUN_DIR\\code\\preflight_readonly_20261114.py

Binding: RK c07fa806dfad (2) P1 PASS+CONDITIONS; GS card r3 0173cd11a090 s13 (P1) + literal lines L101-L126,
Appendix A L001-L100, Appendix B B001-B166, Appendix C C001-C018 (embedded verbatim below, sha-pinned);
RK f83568141488 Q-R1 freeze form; Coder plan r3 33f3ec1e0e07 s7 P1 / s12 BP; RULES d8594afbb02e R62(h).

What it does (and nothing else):
- reads ONLY the targets named by the embedded literal lines (any other path = refuse, exit 78);
- filesystem lines are executed natively and read-only (listing, stat, sha256, Select-String); the
  system lines L101-L104, L106, L124-L126 are run as the literal line text by Windows PowerShell 5.1
  (no shell, fixed argv); L106 per H5 (no -State, no -ErrorAction SilentlyContinue); L125 CommandLine
  is REDACTED before anything is stored;
- R62(h) secrets denylist enforced in code: a denylisted name is never opened (exit 78);
- no network: no network module is imported and an audit hook refuses any socket use (exit 78);
- writes ONLY new files (exclusive create) in RUN_DIR\\preflight\\ (which must already exist and be
  empty); one receipt <= 5 MB, no truncation path (over size = exit 78);
- Q-R1: re-computes the third-party freeze text (header line with interpreter version and executable,
  every distribution as name==version, UTF-8, LF, bytewise sort, duplicate distributions refused) and
  compares its sha256 with the freeze-pack pin; mismatch or no pin = exit 78.
One run only (a second run finds the run marker and exits 78). Any refusal or failure is final (M8).
Built and tested on the box only (synthetic tree). Not a PASS, CLEAR or freeze (R56). Live OFF.
"""
import builtins
import collections
import datetime
import hashlib
import importlib.metadata
import json
import ntpath
import os
import platform
import re
import stat
import subprocess
import sys

EXIT_STOP = 78
SCHEMA = "fulln-p1-preflight-receipt-v1"
CARD_SHA256 = "0173cd11a090966cab8320d0e6e8fcc79495eae9fe4b48de95e63b334e5f2fb7"
RULES_SHA256 = "d8594afbb02e1e5451ff5d7b71bea32bed8a3eeea0a5ed433d87400a892376de"
RUN_DIR = "C:\\DoxxedCrypto\\runs\\btc-fulln-480m-20261114"
PREFLIGHT_DIR = RUN_DIR + "\\preflight"
RUN_MARKER = "PREFLIGHT-RUN.started"
RECEIPT_NAME = "preflight-receipt-20261114.json"
STOP_NOTE_NAME = "preflight-STOP-20261114.json"
RECEIPT_MAX_BYTES = 5_000_000
LINE_OUTPUT_MAX_CHARS = 1_000_000
EXTERNAL_DATA = "C:\\DoxxedCrypto\\external\\ext2\\external-data"
POWERSHELL_EXE = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
SYSTEM_LINE_TIMEOUT_S = 300
SYSTEM_LINE_IDS = ("L101", "L102", "L103", "L104", "L106", "L124", "L125", "L126")
# Freeze-pack pin of the Q-R1 freeze-text sha256. None = not pinned yet -> exit 78 (a new script sha carries it).
EXPECTED_FREEZE_SHA256 = None

# ---------------------------------------------------------------- literal lines (card r3, verbatim)
LINES_TEXT = r"""L101  Get-CimInstance -ClassName Win32_Volume -Filter "DriveLetter='C:'" | Select-Object -Property Capacity,FreeSpace,FileSystem,BlockSize
L102  Get-CimInstance -ClassName Win32_OperatingSystem | Select-Object -Property TotalVisibleMemorySize,FreePhysicalMemory
L103  Get-CimInstance -ClassName Win32_VideoController | Select-Object -Property Name,PNPDeviceID,DriverVersion,AdapterRAM,Status
L104  Get-CimInstance -ClassName Win32_Processor | Select-Object -Property Name,NumberOfCores,NumberOfLogicalProcessors
L105  Get-Item -LiteralPath 'C:\Users\danis\AppData\Local\Programs\Python\Python311\python.exe' | Select-Object -Property FullName,Length,LastWriteTime
L106  Get-NetTCPConnection -LocalPort 7002,9021 | Select-Object -Property LocalAddress,LocalPort,State,OwningProcess
L107  Get-Item -LiteralPath 'C:\DoxxedCrypto\data\collector-r3-20261110\runtime.journal' | Select-Object -Property LastWriteTime,Length
L108  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_search.py'
L109  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_run.py'
L110  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_build.py'
L111  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_lag.py'
L112  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_registry.py'
L113  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\gpu_eval.py'
L114  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\gpu_kernel.py'
L115  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_lattice.py'
L116  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\gpu_prod_path.py'
L117  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_discovery_scores.py'
L118  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_ci_ready.py'
L119  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ext2_search.py'
L120  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\configs\phase_b_search.json'
L121  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\code' -File -Filter 'gpu_*' | Select-Object -Property Name,Length,LastWriteTime
L122  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0' | Select-Object -Property Name,Mode
L123  Test-Path -LiteralPath 'C:\DoxxedCrypto\runs\btc-fulln-480m-20261114'
L124  Get-Process -Name python | Select-Object -Property Id,StartTime,WorkingSet64,PrivateMemorySize64
L125  Get-CimInstance -ClassName Win32_Process -Filter "Name = 'python.exe'" | Select-Object -Property ProcessId,CommandLine
L126  Get-Host | Select-Object -Property Name,Version
L001  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2024-10-*.jsonl.gz' | Select-Object -Property Name,Length
L002  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2024-11-*.jsonl.gz' | Select-Object -Property Name,Length
L003  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2024-12-*.jsonl.gz' | Select-Object -Property Name,Length
L004  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-01-*.jsonl.gz' | Select-Object -Property Name,Length
L005  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-02-*.jsonl.gz' | Select-Object -Property Name,Length
L006  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-03-*.jsonl.gz' | Select-Object -Property Name,Length
L007  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-04-*.jsonl.gz' | Select-Object -Property Name,Length
L008  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-05-*.jsonl.gz' | Select-Object -Property Name,Length
L009  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-06-*.jsonl.gz' | Select-Object -Property Name,Length
L010  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-07-*.jsonl.gz' | Select-Object -Property Name,Length
L011  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-08-*.jsonl.gz' | Select-Object -Property Name,Length
L012  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-09-*.jsonl.gz' | Select-Object -Property Name,Length
L013  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-10-*.jsonl.gz' | Select-Object -Property Name,Length
L014  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-11-*.jsonl.gz' | Select-Object -Property Name,Length
L015  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2025-12-*.jsonl.gz' | Select-Object -Property Name,Length
L016  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-01-*.jsonl.gz' | Select-Object -Property Name,Length
L017  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-02-*.jsonl.gz' | Select-Object -Property Name,Length
L018  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-03-*.jsonl.gz' | Select-Object -Property Name,Length
L019  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-04-*.jsonl.gz' | Select-Object -Property Name,Length
L020  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-05-*.jsonl.gz' | Select-Object -Property Name,Length
L021  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-06-*.jsonl.gz' | Select-Object -Property Name,Length
L022  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-07-*.jsonl.gz' | Select-Object -Property Name,Length
L023  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-08-*.jsonl.gz' | Select-Object -Property Name,Length
L024  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-09-*.jsonl.gz' | Select-Object -Property Name,Length
L025  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades' -File -Filter '2026-10-*.jsonl.gz' | Select-Object -Property Name,Length
L026  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2024-10-*.jsonl.gz' | Select-Object -Property Name,Length
L027  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2024-11-*.jsonl.gz' | Select-Object -Property Name,Length
L028  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2024-12-*.jsonl.gz' | Select-Object -Property Name,Length
L029  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-01-*.jsonl.gz' | Select-Object -Property Name,Length
L030  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-02-*.jsonl.gz' | Select-Object -Property Name,Length
L031  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-03-*.jsonl.gz' | Select-Object -Property Name,Length
L032  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-04-*.jsonl.gz' | Select-Object -Property Name,Length
L033  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-05-*.jsonl.gz' | Select-Object -Property Name,Length
L034  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-06-*.jsonl.gz' | Select-Object -Property Name,Length
L035  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-07-*.jsonl.gz' | Select-Object -Property Name,Length
L036  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-08-*.jsonl.gz' | Select-Object -Property Name,Length
L037  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-09-*.jsonl.gz' | Select-Object -Property Name,Length
L038  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-10-*.jsonl.gz' | Select-Object -Property Name,Length
L039  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-11-*.jsonl.gz' | Select-Object -Property Name,Length
L040  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2025-12-*.jsonl.gz' | Select-Object -Property Name,Length
L041  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-01-*.jsonl.gz' | Select-Object -Property Name,Length
L042  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-02-*.jsonl.gz' | Select-Object -Property Name,Length
L043  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-03-*.jsonl.gz' | Select-Object -Property Name,Length
L044  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-04-*.jsonl.gz' | Select-Object -Property Name,Length
L045  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-05-*.jsonl.gz' | Select-Object -Property Name,Length
L046  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-06-*.jsonl.gz' | Select-Object -Property Name,Length
L047  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-07-*.jsonl.gz' | Select-Object -Property Name,Length
L048  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-08-*.jsonl.gz' | Select-Object -Property Name,Length
L049  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-09-*.jsonl.gz' | Select-Object -Property Name,Length
L050  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m' -File -Filter '2026-10-*.jsonl.gz' | Select-Object -Property Name,Length
L051  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2024-10-*.jsonl.gz' | Select-Object -Property Hash,Path
L052  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2024-11-*.jsonl.gz' | Select-Object -Property Hash,Path
L053  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2024-12-*.jsonl.gz' | Select-Object -Property Hash,Path
L054  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-01-*.jsonl.gz' | Select-Object -Property Hash,Path
L055  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-02-*.jsonl.gz' | Select-Object -Property Hash,Path
L056  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-03-*.jsonl.gz' | Select-Object -Property Hash,Path
L057  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-04-*.jsonl.gz' | Select-Object -Property Hash,Path
L058  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-05-*.jsonl.gz' | Select-Object -Property Hash,Path
L059  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-06-*.jsonl.gz' | Select-Object -Property Hash,Path
L060  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-07-*.jsonl.gz' | Select-Object -Property Hash,Path
L061  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-08-*.jsonl.gz' | Select-Object -Property Hash,Path
L062  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-09-*.jsonl.gz' | Select-Object -Property Hash,Path
L063  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-10-*.jsonl.gz' | Select-Object -Property Hash,Path
L064  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-11-*.jsonl.gz' | Select-Object -Property Hash,Path
L065  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2025-12-*.jsonl.gz' | Select-Object -Property Hash,Path
L066  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-01-*.jsonl.gz' | Select-Object -Property Hash,Path
L067  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-02-*.jsonl.gz' | Select-Object -Property Hash,Path
L068  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-03-*.jsonl.gz' | Select-Object -Property Hash,Path
L069  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-04-*.jsonl.gz' | Select-Object -Property Hash,Path
L070  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-05-*.jsonl.gz' | Select-Object -Property Hash,Path
L071  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-06-*.jsonl.gz' | Select-Object -Property Hash,Path
L072  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-07-*.jsonl.gz' | Select-Object -Property Hash,Path
L073  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-08-*.jsonl.gz' | Select-Object -Property Hash,Path
L074  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-09-*.jsonl.gz' | Select-Object -Property Hash,Path
L075  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\trades\2026-10-*.jsonl.gz' | Select-Object -Property Hash,Path
L076  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2024-10-*.jsonl.gz' | Select-Object -Property Hash,Path
L077  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2024-11-*.jsonl.gz' | Select-Object -Property Hash,Path
L078  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2024-12-*.jsonl.gz' | Select-Object -Property Hash,Path
L079  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-01-*.jsonl.gz' | Select-Object -Property Hash,Path
L080  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-02-*.jsonl.gz' | Select-Object -Property Hash,Path
L081  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-03-*.jsonl.gz' | Select-Object -Property Hash,Path
L082  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-04-*.jsonl.gz' | Select-Object -Property Hash,Path
L083  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-05-*.jsonl.gz' | Select-Object -Property Hash,Path
L084  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-06-*.jsonl.gz' | Select-Object -Property Hash,Path
L085  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-07-*.jsonl.gz' | Select-Object -Property Hash,Path
L086  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-08-*.jsonl.gz' | Select-Object -Property Hash,Path
L087  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-09-*.jsonl.gz' | Select-Object -Property Hash,Path
L088  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-10-*.jsonl.gz' | Select-Object -Property Hash,Path
L089  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-11-*.jsonl.gz' | Select-Object -Property Hash,Path
L090  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2025-12-*.jsonl.gz' | Select-Object -Property Hash,Path
L091  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-01-*.jsonl.gz' | Select-Object -Property Hash,Path
L092  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-02-*.jsonl.gz' | Select-Object -Property Hash,Path
L093  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-03-*.jsonl.gz' | Select-Object -Property Hash,Path
L094  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-04-*.jsonl.gz' | Select-Object -Property Hash,Path
L095  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-05-*.jsonl.gz' | Select-Object -Property Hash,Path
L096  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-06-*.jsonl.gz' | Select-Object -Property Hash,Path
L097  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-07-*.jsonl.gz' | Select-Object -Property Hash,Path
L098  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-08-*.jsonl.gz' | Select-Object -Property Hash,Path
L099  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-09-*.jsonl.gz' | Select-Object -Property Hash,Path
L100  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex\tBTCF0USTF0\candles1m\2026-10-*.jsonl.gz' | Select-Object -Property Hash,Path
B001  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2024-10-*' | Select-Object -Property Name,Length
B002  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2024-11-*' | Select-Object -Property Name,Length
B003  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2024-12-*' | Select-Object -Property Name,Length
B004  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-01-*' | Select-Object -Property Name,Length
B005  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-02-*' | Select-Object -Property Name,Length
B006  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-03-*' | Select-Object -Property Name,Length
B007  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-04-*' | Select-Object -Property Name,Length
B008  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-05-*' | Select-Object -Property Name,Length
B009  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-06-*' | Select-Object -Property Name,Length
B010  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-07-*' | Select-Object -Property Name,Length
B011  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-08-*' | Select-Object -Property Name,Length
B012  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-09-*' | Select-Object -Property Name,Length
B013  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-10-*' | Select-Object -Property Name,Length
B014  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-11-*' | Select-Object -Property Name,Length
B015  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2025-12-*' | Select-Object -Property Name,Length
B016  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-01-*' | Select-Object -Property Name,Length
B017  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-02-*' | Select-Object -Property Name,Length
B018  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-03-*' | Select-Object -Property Name,Length
B019  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-04-*' | Select-Object -Property Name,Length
B020  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-05-*' | Select-Object -Property Name,Length
B021  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-06-*' | Select-Object -Property Name,Length
B022  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-07-*' | Select-Object -Property Name,Length
B023  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-08-*' | Select-Object -Property Name,Length
B024  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-09-*' | Select-Object -Property Name,Length
B025  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT' -File -Filter 'BTCUSDT-metrics-2026-10-*' | Select-Object -Property Name,Length
B026  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2024-10-*' | Select-Object -Property Name,Length
B027  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2024-11-*' | Select-Object -Property Name,Length
B028  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2024-12-*' | Select-Object -Property Name,Length
B029  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-01-*' | Select-Object -Property Name,Length
B030  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-02-*' | Select-Object -Property Name,Length
B031  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-03-*' | Select-Object -Property Name,Length
B032  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-04-*' | Select-Object -Property Name,Length
B033  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-05-*' | Select-Object -Property Name,Length
B034  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-06-*' | Select-Object -Property Name,Length
B035  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-07-*' | Select-Object -Property Name,Length
B036  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-08-*' | Select-Object -Property Name,Length
B037  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-09-*' | Select-Object -Property Name,Length
B038  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-10-*' | Select-Object -Property Name,Length
B039  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-11-*' | Select-Object -Property Name,Length
B040  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2025-12-*' | Select-Object -Property Name,Length
B041  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-01-*' | Select-Object -Property Name,Length
B042  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-02-*' | Select-Object -Property Name,Length
B043  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-03-*' | Select-Object -Property Name,Length
B044  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-04-*' | Select-Object -Property Name,Length
B045  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-05-*' | Select-Object -Property Name,Length
B046  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-06-*' | Select-Object -Property Name,Length
B047  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-07-*' | Select-Object -Property Name,Length
B048  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-08-*' | Select-Object -Property Name,Length
B049  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-09-*' | Select-Object -Property Name,Length
B050  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT' -File -Filter 'BTCUSDT-bookDepth-2026-10-*' | Select-Object -Property Name,Length
B051  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2024-10-*' | Select-Object -Property Name,Length
B052  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2024-11-*' | Select-Object -Property Name,Length
B053  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2024-12-*' | Select-Object -Property Name,Length
B054  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-01-*' | Select-Object -Property Name,Length
B055  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-02-*' | Select-Object -Property Name,Length
B056  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-03-*' | Select-Object -Property Name,Length
B057  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-04-*' | Select-Object -Property Name,Length
B058  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-05-*' | Select-Object -Property Name,Length
B059  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-06-*' | Select-Object -Property Name,Length
B060  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-07-*' | Select-Object -Property Name,Length
B061  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-08-*' | Select-Object -Property Name,Length
B062  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-09-*' | Select-Object -Property Name,Length
B063  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-10-*' | Select-Object -Property Name,Length
B064  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-11-*' | Select-Object -Property Name,Length
B065  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2025-12-*' | Select-Object -Property Name,Length
B066  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-01-*' | Select-Object -Property Name,Length
B067  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-02-*' | Select-Object -Property Name,Length
B068  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-03-*' | Select-Object -Property Name,Length
B069  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-04-*' | Select-Object -Property Name,Length
B070  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-05-*' | Select-Object -Property Name,Length
B071  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-06-*' | Select-Object -Property Name,Length
B072  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-07-*' | Select-Object -Property Name,Length
B073  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-08-*' | Select-Object -Property Name,Length
B074  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-09-*' | Select-Object -Property Name,Length
B075  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT' -File -Filter 'BTCUSDT-aggTrades-2026-10-*' | Select-Object -Property Name,Length
B076  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\aggTrades\BTCUSDT' -File | Select-Object -Property Name,Length
B077  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\monthly\aggTrades\BTCUSDT' -File | Select-Object -Property Name,Length
B078  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\monthly\fundingRate\BTCUSDT' -File | Select-Object -Property Name,Length
B079  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\monthly\aggTrades\BTCUSDT' -File | Select-Object -Property Name,Length
B080  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex-stats1' -File | Select-Object -Property Name,Length
B081  Get-Item -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\deribit-dvol\BTC_DVOL_1h.jsonl' | Select-Object -Property FullName,Length,LastWriteTime
B082  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data' | Select-Object -Property Name,Mode
B083  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um' | Select-Object -Property Name,Mode
B084  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot' | Select-Object -Property Name,Mode
B085  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\deribit-dvol' -File | Select-Object -Property Name,Length
B086  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2024-10-*' | Select-Object -Property Hash,Path
B087  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2024-11-*' | Select-Object -Property Hash,Path
B088  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2024-12-*' | Select-Object -Property Hash,Path
B089  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-01-*' | Select-Object -Property Hash,Path
B090  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-02-*' | Select-Object -Property Hash,Path
B091  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-03-*' | Select-Object -Property Hash,Path
B092  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-04-*' | Select-Object -Property Hash,Path
B093  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-05-*' | Select-Object -Property Hash,Path
B094  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-06-*' | Select-Object -Property Hash,Path
B095  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-07-*' | Select-Object -Property Hash,Path
B096  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-08-*' | Select-Object -Property Hash,Path
B097  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-09-*' | Select-Object -Property Hash,Path
B098  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-10-*' | Select-Object -Property Hash,Path
B099  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-11-*' | Select-Object -Property Hash,Path
B100  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2025-12-*' | Select-Object -Property Hash,Path
B101  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-01-*' | Select-Object -Property Hash,Path
B102  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-02-*' | Select-Object -Property Hash,Path
B103  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-03-*' | Select-Object -Property Hash,Path
B104  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-04-*' | Select-Object -Property Hash,Path
B105  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-05-*' | Select-Object -Property Hash,Path
B106  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-06-*' | Select-Object -Property Hash,Path
B107  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-07-*' | Select-Object -Property Hash,Path
B108  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-08-*' | Select-Object -Property Hash,Path
B109  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-09-*' | Select-Object -Property Hash,Path
B110  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\metrics\BTCUSDT\BTCUSDT-metrics-2026-10-*' | Select-Object -Property Hash,Path
B111  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2024-10-*' | Select-Object -Property Hash,Path
B112  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2024-11-*' | Select-Object -Property Hash,Path
B113  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2024-12-*' | Select-Object -Property Hash,Path
B114  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-01-*' | Select-Object -Property Hash,Path
B115  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-02-*' | Select-Object -Property Hash,Path
B116  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-03-*' | Select-Object -Property Hash,Path
B117  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-04-*' | Select-Object -Property Hash,Path
B118  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-05-*' | Select-Object -Property Hash,Path
B119  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-06-*' | Select-Object -Property Hash,Path
B120  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-07-*' | Select-Object -Property Hash,Path
B121  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-08-*' | Select-Object -Property Hash,Path
B122  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-09-*' | Select-Object -Property Hash,Path
B123  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-10-*' | Select-Object -Property Hash,Path
B124  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-11-*' | Select-Object -Property Hash,Path
B125  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2025-12-*' | Select-Object -Property Hash,Path
B126  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-01-*' | Select-Object -Property Hash,Path
B127  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-02-*' | Select-Object -Property Hash,Path
B128  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-03-*' | Select-Object -Property Hash,Path
B129  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-04-*' | Select-Object -Property Hash,Path
B130  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-05-*' | Select-Object -Property Hash,Path
B131  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-06-*' | Select-Object -Property Hash,Path
B132  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-07-*' | Select-Object -Property Hash,Path
B133  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-08-*' | Select-Object -Property Hash,Path
B134  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-09-*' | Select-Object -Property Hash,Path
B135  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\bookDepth\BTCUSDT\BTCUSDT-bookDepth-2026-10-*' | Select-Object -Property Hash,Path
B136  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2024-10-*' | Select-Object -Property Hash,Path
B137  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2024-11-*' | Select-Object -Property Hash,Path
B138  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2024-12-*' | Select-Object -Property Hash,Path
B139  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-01-*' | Select-Object -Property Hash,Path
B140  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-02-*' | Select-Object -Property Hash,Path
B141  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-03-*' | Select-Object -Property Hash,Path
B142  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-04-*' | Select-Object -Property Hash,Path
B143  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-05-*' | Select-Object -Property Hash,Path
B144  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-06-*' | Select-Object -Property Hash,Path
B145  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-07-*' | Select-Object -Property Hash,Path
B146  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-08-*' | Select-Object -Property Hash,Path
B147  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-09-*' | Select-Object -Property Hash,Path
B148  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-10-*' | Select-Object -Property Hash,Path
B149  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-11-*' | Select-Object -Property Hash,Path
B150  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2025-12-*' | Select-Object -Property Hash,Path
B151  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-01-*' | Select-Object -Property Hash,Path
B152  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-02-*' | Select-Object -Property Hash,Path
B153  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-03-*' | Select-Object -Property Hash,Path
B154  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-04-*' | Select-Object -Property Hash,Path
B155  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-05-*' | Select-Object -Property Hash,Path
B156  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-06-*' | Select-Object -Property Hash,Path
B157  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-07-*' | Select-Object -Property Hash,Path
B158  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-08-*' | Select-Object -Property Hash,Path
B159  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-09-*' | Select-Object -Property Hash,Path
B160  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\daily\aggTrades\BTCUSDT\BTCUSDT-aggTrades-2026-10-*' | Select-Object -Property Hash,Path
B161  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\daily\aggTrades\BTCUSDT\*' | Select-Object -Property Hash,Path
B162  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\monthly\aggTrades\BTCUSDT\*' | Select-Object -Property Hash,Path
B163  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\futures\um\monthly\fundingRate\BTCUSDT\*' | Select-Object -Property Hash,Path
B164  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\binance\spot\monthly\aggTrades\BTCUSDT\*' | Select-Object -Property Hash,Path
B165  Get-FileHash -Algorithm SHA256 -Path 'C:\DoxxedCrypto\external\ext2\external-data\bitfinex-stats1\*' | Select-Object -Property Hash,Path
B166  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\external-data\deribit-dvol\BTC_DVOL_1h.jsonl'
C001  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_search.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C002  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_run.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C003  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_build.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C004  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_lag.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C005  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_registry.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C006  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\gpu_eval.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C007  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\gpu_kernel.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C008  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_lattice.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C009  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\gpu_prod_path.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C010  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_discovery_scores.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C011  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\phase_b_ci_ready.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C012  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ext2_search.py' -Pattern '^\s*(import|from)\s' | Select-Object -Property LineNumber,Line
C013  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ext2_search.py' -Pattern 'external-data|def load_' | Select-Object -Property LineNumber,Line
C014  Select-String -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\ci_discovery_scores.py' -Pattern 'external-data|def load_' | Select-Object -Property LineNumber,Line
C015  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\code' -File | Select-Object -Property Name,Length,LastWriteTime
C016  Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\DoxxedCrypto\external\ext2\code\configs\phase_b_ci_registration.json'
C017  Get-ChildItem -LiteralPath 'C:\DoxxedCrypto\external\ext2\code' -Directory | Select-Object -Property Name,Mode
C018  Get-ChildItem -LiteralPath 'C:\Users\danis\AppData\Local\Programs\Python\Python311\Lib\site-packages' -Directory -Filter '*.dist-info' | Select-Object -Property Name
"""
LINES_SHA256 = "39022b5b487de3f8a3fb78557fc755054893005a12b82d58564510c77d6bf9ed"
LINES_COUNT = 310


class PreflightStop(Exception):
    exit_code = EXIT_STOP

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = str(reason)


# ---------------------------------------------------------------- guard (no network, no writes outside)
_GUARD = {"armed": False, "write_dir": None, "allowed_argv": ()}
_NET_EVENTS = ("socket.", "urllib.", "http.", "ftplib.", "smtplib.", "nntplib.", "poplib.", "imaplib.", "telnetlib.")
_WRITE_EVENTS = ("os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace", "os.mkdir", "os.link",
                 "os.symlink", "os.truncate", "os.chmod", "os.chown", "os.utime", "shutil.", "os.makedirs")
_WFLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _under_dir(path, d):
    p = ntpath.normcase(os.path.abspath(os.fspath(path))) if os.name == "nt" else os.path.abspath(os.fspath(path))
    q = ntpath.normcase(os.path.abspath(d)) if os.name == "nt" else os.path.abspath(d)
    return os.path.dirname(p) == q


def _hook(event, args):
    if not _GUARD["armed"]:
        return
    if event.startswith(_NET_EVENTS):
        raise PreflightStop("network use refused (%s)" % event)
    if event.startswith(_WRITE_EVENTS):
        raise PreflightStop("filesystem change refused (%s)" % event)
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WFLAGS)
        if writing:
            exclusive = (isinstance(mode, str) and "x" in mode) or (isinstance(flags, int) and flags & os.O_EXCL)
            if not (isinstance(path, (str, bytes)) and exclusive and _GUARD["write_dir"]
                    and _under_dir(os.fsdecode(path), _GUARD["write_dir"])):
                raise PreflightStop("write-open refused outside RUN_DIR\\preflight or without exclusive create: %r" % (path,))
    if event == "subprocess.Popen":
        exe, argv = args[0], args[1]
        if tuple(argv) not in _GUARD["allowed_argv"]:
            raise PreflightStop("process start refused: %r" % (exe,))


_HOOK_INSTALLED = []


def _install_hook():
    if not _HOOK_INSTALLED:
        sys.addaudithook(_hook)
        _HOOK_INSTALLED.append(True)


# ---------------------------------------------------------------- R62(h) secrets denylist
_DENY_GLOBS = (".env*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*", ".git-credentials", ".netrc",
               ".npmrc", ".pypirc")
_DENY_WORDS = ("secret", "token", "credential", "apikey")
_DENY_DIR_SEGMENTS = (".ssh", ".aws", ".azure", ".docker", ".gnupg")  # R62 addendum 3 M5 hidden dot-dirs (+ .config\gh below)
_DENY_DIR_SEQUENCES = (("google", "chrome", "user data"), ("microsoft", "edge", "user data"),
                       ("mozilla", "firefox", "profiles"), ("bravesoftware",), ("microsoft", "credentials"),
                       ("microsoft", "vault"), ("microsoft", "protect"), ("keychains",), (".config", "gh"))
_CONFIG_EXT = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".txt")


def _glob_re(pattern):
    if "[" in pattern or "]" in pattern:
        raise PreflightStop("bracket wildcards are not supported: %r" % pattern)
    return re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern),
                      re.IGNORECASE | re.DOTALL)


_DENY_RES = tuple(_glob_re(g) for g in _DENY_GLOBS)


def is_denied(winpath):
    """R62(h): True if the path names a secret file or a protected directory (never opened)."""
    segs = [s.casefold() for s in ntpath.normpath(winpath).split("\\") if s]
    name = segs[-1] if segs else ""
    if any(r.fullmatch(name) for r in _DENY_RES):
        return True
    if any(w in s for s in segs for w in _DENY_WORDS):
        return True
    if any(s in _DENY_DIR_SEGMENTS for s in segs[:-1]):
        return True
    for seq in _DENY_DIR_SEQUENCES:
        n = len(seq)
        if any(tuple(segs[i:i + n]) == seq for i in range(len(segs) - n + 1)):
            return True
    if ("bitfinex" in name and name.endswith(_CONFIG_EXT)) or ("live" in name and "key" in name):
        return True
    return False


# ---------------------------------------------------------------- line table
_R_GCI = re.compile(r"^Get-ChildItem -LiteralPath '([^']+)'(?: (-File|-Directory))?(?: -Filter '([^']+)')? \| "
                    r"Select-Object -Property ([A-Za-z,]+)$")
_R_HASH_WILD = re.compile(r"^Get-FileHash -Algorithm SHA256 -Path '([^']+)' \| Select-Object -Property Hash,Path$")
_R_HASH_LIT = re.compile(r"^Get-FileHash -Algorithm SHA256 -LiteralPath '([^']+)'$")
_R_ITEM = re.compile(r"^Get-Item -LiteralPath '([^']+)' \| Select-Object -Property ([A-Za-z,]+)$")
_R_TEST = re.compile(r"^Test-Path -LiteralPath '([^']+)'$")
_R_SS = re.compile(r"^Select-String -LiteralPath '([^']+)' -Pattern '([^']+)' \| Select-Object -Property LineNumber,Line$")
_R_SYS = re.compile(r"^(Get-CimInstance|Get-NetTCPConnection|Get-Process|Get-Host)( |$)")
_GCI_PROPS = {"Name", "Length", "LastWriteTime", "Mode"}
_ITEM_PROPS = {"FullName", "Length", "LastWriteTime"}


def expected_ids():
    return (["L%03d" % i for i in range(101, 127)] + ["L%03d" % i for i in range(1, 101)]
            + ["B%03d" % i for i in range(1, 167)] + ["C%03d" % i for i in range(1, 19)])


def load_lines(text=None, expected_sha=None):
    text = LINES_TEXT if text is None else text
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if sha != (LINES_SHA256 if expected_sha is None else expected_sha):
        raise PreflightStop("literal line table sha mismatch (%s) - extra or edited line refused" % sha[:12])
    out = []
    for raw in text.split("\n"):
        if not raw:
            continue
        lid, sep, body = raw.partition("  ")
        if not sep or not re.fullmatch(r"[LBC]\d{3}", lid):
            raise PreflightStop("malformed line %r" % raw[:40])
        out.append((lid, body))
    if [x[0] for x in out] != expected_ids() or len(out) != LINES_COUNT:
        raise PreflightStop("line ids are not exactly L101-L126, L001-L100, B001-B166, C001-C018")
    return out


def parse_line(lid, body):
    m = _R_GCI.match(body)
    if m:
        props = m.group(4).split(",")
        if not set(props) <= _GCI_PROPS:
            raise PreflightStop("%s: unsupported properties %s" % (lid, props))
        return {"kind": "listing", "dir": m.group(1), "only": m.group(2), "filter": m.group(3), "props": props}
    m = _R_HASH_WILD.match(body)
    if m:
        d, pat = ntpath.split(m.group(1))
        if any(c in d for c in "*?[]"):
            raise PreflightStop("%s: wildcard outside the last path segment" % lid)
        return {"kind": "hash_wild", "dir": d, "filter": pat}
    m = _R_HASH_LIT.match(body)
    if m:
        return {"kind": "hash_lit", "path": m.group(1)}
    m = _R_ITEM.match(body)
    if m:
        props = m.group(2).split(",")
        if not set(props) <= _ITEM_PROPS:
            raise PreflightStop("%s: unsupported properties %s" % (lid, props))
        return {"kind": "item", "path": m.group(1), "props": props}
    m = _R_TEST.match(body)
    if m:
        return {"kind": "test_path", "path": m.group(1)}
    m = _R_SS.match(body)
    if m:
        return {"kind": "select_string", "path": m.group(1), "pattern": m.group(2)}
    if _R_SYS.match(body) and lid in SYSTEM_LINE_IDS:
        return {"kind": "system"}
    raise PreflightStop("%s: line form not recognised - refused" % lid)


# ---------------------------------------------------------------- config
class Config(object):
    """Production: no arguments. Tests pass a path map (literal Windows prefix -> box dir) and fakes."""

    def __init__(self, path_map=None, system_runner=None, distributions=None, expected_freeze_sha256=None,
                 receipt_max_bytes=RECEIPT_MAX_BYTES, lines_text=None, lines_sha256=None, production=False):
        self.path_map = tuple(path_map or ())
        self.system_runner = system_runner
        self.distributions = distributions
        self.expected_freeze_sha256 = expected_freeze_sha256
        self.receipt_max_bytes = receipt_max_bytes
        self.lines_text = lines_text
        self.lines_sha256 = lines_sha256
        self.production = production

    def host(self, winpath):
        if self.production:
            return winpath
        for pre, d in self.path_map:
            if winpath.casefold() == pre.casefold() or winpath.casefold().startswith(pre.casefold() + "\\"):
                rest = winpath[len(pre):].lstrip("\\")
                return os.path.join(d, *rest.split("\\")) if rest else d
        raise PreflightStop("no host mapping for %s" % winpath)


def production_config():
    return Config(system_runner=powershell_runner, expected_freeze_sha256=EXPECTED_FREEZE_SHA256, production=True)


def _ps_argv(body):
    return (POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command", body)


def powershell_runner(body):
    """Run one literal system line in Windows PowerShell 5.1 (fixed argv, no shell). Returns (rc, out, err)."""
    p = subprocess.run(list(_ps_argv(body)), capture_output=True, timeout=SYSTEM_LINE_TIMEOUT_S, shell=False)
    dec = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)
    return p.returncode, dec(p.stdout), dec(p.stderr)


# ---------------------------------------------------------------- Q-R1 freeze text (RK f83568141488)
def _norm_dist(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def freeze_text(distributions=None):
    """Header + every distribution as 'name==version' (normalised name), UTF-8, LF, bytewise sort; duplicates = 78."""
    dists = list(importlib.metadata.distributions()) if distributions is None else list(distributions)
    lines, names = [], []
    for d in dists:
        name, ver = d.metadata["Name"], d.version
        if not name or not ver or "\n" in name or "\n" in ver or "\r" in name or "\r" in ver:
            raise PreflightStop("distribution without a clean name/version: %r" % (name,))
        n = _norm_dist(name)
        names.append(n)
        lines.append(("%s==%s" % (n, ver)).encode("utf-8"))
    dup = sorted(k for k, c in collections.Counter(names).items() if c > 1)
    if dup:
        raise PreflightStop("duplicate distributions installed: %s" % dup)
    header = ("#interpreter version=%s executable=%s" % (platform.python_version(), sys.executable)).encode("utf-8")
    return header + b"\n" + b"".join(x + b"\n" for x in sorted(lines))


# ---------------------------------------------------------------- filesystem ops (read-only)
def _mode(st):
    attrs = getattr(st, "st_file_attributes", None)
    d = "d" if stat.S_ISDIR(st.st_mode) else "-"
    if attrs is None:
        return d + "-----"
    a = "a" if attrs & 0x20 else "-"
    r = "r" if attrs & 0x1 else "-"
    h = "h" if attrs & 0x2 else "-"
    s = "s" if attrs & 0x4 else "-"
    l = "l" if attrs & 0x400 else "-"
    return d + a + r + h + s + l


def _utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Reader(object):
    """All reads go through here; every target must come from a parsed literal line."""

    def __init__(self, cfg, ops):
        self.cfg = cfg
        self.files_ok = set()
        self.dirs_ok = set()
        self.wild_ok = []
        for lid, op in ops:
            if op["kind"] in ("hash_lit", "select_string"):
                self.files_ok.add(op["path"].casefold())
            elif op["kind"] in ("listing",):
                self.dirs_ok.add(op["dir"].casefold())
            elif op["kind"] == "hash_wild":
                self.dirs_ok.add(op["dir"].casefold())
                self.wild_ok.append((op["dir"].casefold(), _glob_re(op["filter"])))

    def _content_allowed(self, winpath):
        if winpath.casefold() in self.files_ok:
            return True
        d, name = ntpath.split(winpath)
        return any(d.casefold() == wd and r.fullmatch(name) for wd, r in self.wild_ok)

    def read_bytes_sha(self, winpath):
        if not self._content_allowed(winpath):
            raise PreflightStop("read of a non-literal path refused: %s" % winpath)
        if is_denied(winpath):
            raise PreflightStop("R62(h) denylisted name - never opened: %s" % winpath)
        h = hashlib.sha256()
        n = 0
        with builtins.open(self.cfg.host(winpath), "rb") as f:  # read-only
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                h.update(b)
                n += len(b)
        return n, h.hexdigest()

    def read_text_lines(self, winpath):
        if not self._content_allowed(winpath):
            raise PreflightStop("read of a non-literal path refused: %s" % winpath)
        if is_denied(winpath):
            raise PreflightStop("R62(h) denylisted name - never opened: %s" % winpath)
        with builtins.open(self.cfg.host(winpath), "rb") as f:  # read-only
            data = f.read()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        return data.decode("utf-8", "replace").splitlines()

    def list_dir(self, windir):
        if windir.casefold() not in self.dirs_ok:
            raise PreflightStop("listing of a non-literal directory refused: %s" % windir)
        host = self.cfg.host(windir)
        if not os.path.isdir(host):
            raise PreflightStop("directory absent (PowerShell would error): %s" % windir)
        out = []
        for e in os.scandir(host):
            st = e.stat(follow_symlinks=False)
            out.append((e.name, st))
        out.sort(key=lambda x: x[0].casefold())
        return out

    def stat_path(self, winpath):
        host = self.cfg.host(winpath)
        try:
            return os.stat(host)
        except FileNotFoundError:
            return None


def _do_listing(rd, op):
    rows = []
    rx = _glob_re(op["filter"]) if op["filter"] else None
    for name, st in rd.list_dir(op["dir"]):
        isdir = stat.S_ISDIR(st.st_mode)
        if op["only"] == "-File" and isdir or op["only"] == "-Directory" and not isdir:
            continue
        if rx is not None and not rx.fullmatch(name):
            continue
        row = {}
        for p in op["props"]:
            row[p] = {"Name": name, "Length": None if isdir else st.st_size, "LastWriteTime": _utc(st.st_mtime),
                      "Mode": _mode(st)}[p]
        rows.append(row)
    return {"rows": rows, "count": len(rows)}


def _do_hash_wild(rd, op):
    rx = _glob_re(op["filter"])
    rows = []
    for name, st in rd.list_dir(op["dir"]):
        if not rx.fullmatch(name):
            continue
        if stat.S_ISDIR(st.st_mode):
            raise PreflightStop("hash wildcard matched a directory: %s\\%s" % (op["dir"], name))
        p = op["dir"] + "\\" + name
        n, h = rd.read_bytes_sha(p)
        if n != st.st_size:
            raise PreflightStop("file changed while hashing: %s" % p)
        rows.append({"Hash": h.upper(), "Path": p, "_length": n})
    return {"rows": rows, "count": len(rows)}


def _do_hash_lit(rd, op):
    st = rd.stat_path(op["path"])
    if st is None or not stat.S_ISREG(st.st_mode):
        raise PreflightStop("file absent: %s" % op["path"])
    n, h = rd.read_bytes_sha(op["path"])
    return {"rows": [{"Algorithm": "SHA256", "Hash": h.upper(), "Path": op["path"], "_length": n}], "count": 1}


def _do_item(rd, op):
    st = rd.stat_path(op["path"])
    if st is None:
        raise PreflightStop("item absent: %s" % op["path"])
    v = {"FullName": op["path"], "Length": None if stat.S_ISDIR(st.st_mode) else st.st_size,
         "LastWriteTime": _utc(st.st_mtime)}
    return {"rows": [{p: v[p] for p in op["props"]}], "count": 1}


def _do_test_path(rd, op):
    return {"rows": [{"Value": rd.stat_path(op["path"]) is not None}], "count": 1}


def _do_select_string(rd, op):
    st = rd.stat_path(op["path"])
    if st is None:
        raise PreflightStop("file absent: %s" % op["path"])
    rx = re.compile(op["pattern"], re.IGNORECASE)
    rows = [{"LineNumber": i, "Line": ln} for i, ln in enumerate(rd.read_text_lines(op["path"]), 1) if rx.search(ln)]
    return {"rows": rows, "count": len(rows)}


_REDACTED = "[REDACTED]"


def redact_l125(text):
    """Keep the header, separator and each ProcessId; every CommandLine (and any other text) is replaced."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or set(s) <= set("- "):
            out.append(ln)
        elif re.fullmatch(r"ProcessId\s+CommandLine", s):
            out.append(ln)
        else:
            m = re.match(r"^\s*(\d+)(\s|$)", ln)
            out.append((m.group(1) + " " + _REDACTED) if m else _REDACTED)
    return "\n".join(out)


_NOT_LISTENING_MSG = "No matching MSFT_NetTCPConnection objects"


def _do_system(cfg, lid, body):
    if cfg.system_runner is None:
        raise PreflightStop("%s: no system runner" % lid)
    rc, out, err = cfg.system_runner(body)
    if len(out) > LINE_OUTPUT_MAX_CHARS or len(err) > LINE_OUTPUT_MAX_CHARS:
        raise PreflightStop("%s: output over %d chars (no truncation path)" % (lid, LINE_OUTPUT_MAX_CHARS))
    if lid == "L125":
        out, err = redact_l125(out), (_REDACTED if err else "")
    if lid == "L106":
        # H5: "No matching" error = not listening; any Listen row = STOP; any other error = STOP;
        # an empty result with no error cannot happen without SilentlyContinue -> STOP.
        if err.strip():
            if _NOT_LISTENING_MSG in err and not out.strip():
                return {"rc": rc, "stdout": out, "stderr": err, "status": "NOT_LISTENING"}
            raise PreflightStop("L106 error other than 'No matching' - STOP")
        if not out.strip():
            raise PreflightStop("L106 empty result without the 'No matching' error - STOP (error suppressed?)")
        if re.search(r"\bListen\b", out):
            raise PreflightStop("L106: LISTEN on 7002/9021 - STOP")
        return {"rc": rc, "stdout": out, "stderr": err, "status": "ROWS_NOT_LISTEN"}
    if rc != 0 or err.strip():
        raise PreflightStop("%s: PowerShell error rc=%s" % (lid, rc))
    return {"rc": rc, "stdout": out, "stderr": err, "status": "OK"}


_DISPATCH = {"listing": _do_listing, "hash_wild": _do_hash_wild, "hash_lit": _do_hash_lit, "item": _do_item,
             "test_path": _do_test_path, "select_string": _do_select_string}


# ---------------------------------------------------------------- pairing + manifests
def pair_listing_and_hash(ops, results):
    """RK 39ea69c1dabb Flag 4: every hash line's return count EQUALS its listing's count (same dir + filter),
    with identical names; per-file length from the listing. Returns the per-file list."""
    listings = {}
    for lid, op in ops:
        if op["kind"] == "listing" and op["only"] == "-File" and "Length" in op["props"]:
            listings[(op["dir"].casefold(), (op["filter"] or "*").casefold())] = lid
    files = []
    for lid, op in ops:
        if op["kind"] != "hash_wild":
            continue
        key = (op["dir"].casefold(), op["filter"].casefold())
        if key not in listings:
            raise PreflightStop("%s: no matching listing line" % lid)
        L = results[listings[key]]
        H = results[lid]
        if L["count"] != H["count"]:
            raise PreflightStop("%s vs %s: hash count %d != listing count %d" % (lid, listings[key], H["count"], L["count"]))
        lens = {r["Name"].casefold(): r["Length"] for r in L["rows"]}
        for r in H["rows"]:
            name = ntpath.basename(r["Path"]).casefold()
            if name not in lens or lens[name] != r["_length"]:
                raise PreflightStop("%s: %s not in listing %s or length differs" % (lid, r["Path"], listings[key]))
            files.append({"path": r["Path"], "length": r["_length"], "sha256": r["Hash"].lower(),
                          "listing_line": listings[key], "hash_line": lid})
    for lid, op in ops:
        if op["kind"] == "hash_lit":
            r = results[lid]["rows"][0]
            files.append({"path": r["Path"], "length": r["_length"], "sha256": r["Hash"].lower(),
                          "listing_line": None, "hash_line": lid})
    seen = set()
    for f in files:
        k = f["path"].casefold()
        if k in seen:
            raise PreflightStop("file hashed twice: %s" % f["path"])
        seen.add(k)
    return files


def manifest_lines(files):
    """'feed/name length sha256' with feed = parent dir relative to external-data ('/' separated); bytewise sorted."""
    per, allv = collections.defaultdict(list), []
    base = EXTERNAL_DATA.casefold() + "\\"
    for f in files:
        if not f["path"].casefold().startswith(base):
            continue
        rel = f["path"][len(base):].replace("\\", "/")
        line = ("%s %d %s" % (rel, f["length"], f["sha256"])).encode("utf-8")
        per[rel.rsplit("/", 1)[0]].append(line)
        allv.append(line)
    mk = lambda ls: b"".join(x + b"\n" for x in sorted(ls))
    out = {k: hashlib.sha256(mk(v)).hexdigest() for k, v in sorted(per.items())}
    out["ALL"] = hashlib.sha256(mk(allv)).hexdigest()
    return out


# ---------------------------------------------------------------- run
def _write_new(path, data):
    with builtins.open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cfg):
    """Execute the literal lines once. Returns 0 on a clean receipt, 78 on any refusal (receipt or STOP note)."""
    pdir = cfg.host(PREFLIGHT_DIR)
    if not os.path.isdir(pdir):
        raise PreflightStop("RUN_DIR\\preflight absent (created only through the R62(o) lane) - refused")
    if os.listdir(pdir):
        raise PreflightStop("RUN_DIR\\preflight not empty - one run only (M8)")
    _install_hook()
    rec = {"schema": SCHEMA, "card_sha256": CARD_SHA256, "rules_sha256": RULES_SHA256, "started_utc": _now(),
           "script": os.path.basename(__file__), "lines": [], "files": [], "manifests": {}, "freeze": None,
           "stops": []}
    _GUARD.update(armed=True, write_dir=pdir, allowed_argv=tuple())
    try:
        _write_new(os.path.join(pdir, RUN_MARKER), (_now() + "\n").encode("utf-8"))
        try:
            rec["lines_sha256"] = hashlib.sha256((cfg.lines_text if cfg.lines_text is not None else LINES_TEXT).encode("utf-8")).hexdigest()
            lines = load_lines(cfg.lines_text, cfg.lines_sha256)
            ops = [(lid, parse_line(lid, body)) for lid, body in lines]
            expected = cfg.expected_freeze_sha256
            ft = freeze_text(cfg.distributions)
            fsha = hashlib.sha256(ft).hexdigest()
            rec["freeze"] = {"sha256": fsha, "expected": expected, "header": ft.split(b"\n", 1)[0].decode("utf-8"),
                             "n_distributions": ft.count(b"\n") - 1}
            if expected is None:
                raise PreflightStop("Q-R1 freeze sha not pinned (freeze pack) - refused")
            if fsha != expected:
                raise PreflightStop("Q-R1 freeze sha mismatch %s != %s" % (fsha[:12], str(expected)[:12]))
            _GUARD["allowed_argv"] = tuple(_ps_argv(body) for lid, body in lines if lid in SYSTEM_LINE_IDS)
            rd = Reader(cfg, ops)
            results = {}
            for (lid, body), (_, op) in zip(lines, ops):
                if op["kind"] == "system":
                    r = _do_system(cfg, lid, body)
                else:
                    r = _DISPATCH[op["kind"]](rd, op)
                results[lid] = r
                rec["lines"].append(dict({"id": lid, "text": body, "kind": op["kind"]}, **{
                    k: v for k, v in r.items()}))
            rec["files"] = pair_listing_and_hash(ops, results)
            rec["manifests"] = manifest_lines(rec["files"])
        except PreflightStop as e:
            rec["stops"].append(e.reason)
        rec["finished_utc"] = _now()
        data = (json.dumps(rec, sort_keys=True, indent=0, ensure_ascii=True) + "\n").encode("utf-8")
        if len(data) > cfg.receipt_max_bytes:
            note = {"schema": SCHEMA, "stops": ["receipt %d B > %d B - no truncation path" % (len(data), cfg.receipt_max_bytes)]
                    + rec["stops"], "finished_utc": rec["finished_utc"]}
            _write_new(os.path.join(pdir, STOP_NOTE_NAME), (json.dumps(note, sort_keys=True) + "\n").encode("utf-8"))
            return EXIT_STOP
        sha = _write_new(os.path.join(pdir, RECEIPT_NAME), data)
        sys.stdout.write("receipt %s sha256 %s stops %d\n" % (RECEIPT_NAME, sha, len(rec["stops"])))
        return EXIT_STOP if rec["stops"] else 0
    finally:
        _GUARD.update(armed=False, write_dir=None, allowed_argv=tuple())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        sys.stderr.write("no arguments accepted\n")
        return EXIT_STOP
    if os.name != "nt":
        sys.stderr.write("laptop (Windows) only\n")
        return EXIT_STOP
    try:
        return run(production_config())
    except PreflightStop as e:
        sys.stderr.write("STOP: %s\n" % e.reason)
        return EXIT_STOP


if __name__ == "__main__":
    sys.exit(main())
