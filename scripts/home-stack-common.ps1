# Shared helpers for home-stack-launcher.ps1 and home-stack-start-all.ps1
param(
  [int]$BridgePort = 7810,
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9001
)

if (-not $scriptDir) {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $repoRoot) {
  $repoRoot = Split-Path -Parent $scriptDir
}
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$cloudflaredPidFile = Join-Path $repoRoot ".home-cloudflared.pid"
$userStoppedFile = Join-Path $repoRoot ".home-stack-user-stopped"

function Get-ResearchStackVersion {
  $configPath = Join-Path $agentDir "combo_pathway_config.py"
  try {
    $configText = Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop
    if ($configText -match 'RESEARCH_STACK_VERSION\s*=\s*"([^"]+)"') {
      return $matches[1]
    }
  } catch { }
  return "unknown"
}

function Set-HomeStackUserStopped {
  Set-Content -Path $userStoppedFile -Value (Get-Date -Format o) -NoNewline
}

function Clear-HomeStackUserStopped {
  Remove-Item $userStoppedFile -Force -ErrorAction SilentlyContinue
}

function Test-HomeStackUserStopped {
  return Test-Path $userStoppedFile
}

# Windows process enumeration (Get-Process/Get-CimInstance) can block for
# minutes when the process/TCP providers are contended.  Health and watchdog
# loops only need a bounded yes/no liveness answer, so use the kernel process
# handle directly instead of enumerating the process table.
function Initialize-HomeStackNativeProcess {
  if ("HomeStackNativeProcess" -as [type]) { return }
  Add-Type @"
using System;
using System.Collections.Generic;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
public static class HomeStackNativeProcess {
  public delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);
  public const uint TH32CS_SNAPPROCESS = 0x00000002;
  const int CCH_RM_SESSION_KEY = 32;
  const int CCH_RM_MAX_APP_NAME = 255;
  const int CCH_RM_MAX_SVC_NAME = 63;
  const int ERROR_MORE_DATA = 234;

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct PROCESSENTRY32 {
    public uint dwSize;
    public uint cntUsage;
    public uint th32ProcessID;
    public IntPtr th32DefaultHeapID;
    public uint th32ModuleID;
    public uint cntThreads;
    public uint th32ParentProcessID;
    public int pcPriClassBase;
    public uint dwFlags;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=260)]
    public string szExeFile;
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct RM_UNIQUE_PROCESS {
    public int dwProcessId;
    public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
  }

  public enum RM_APP_TYPE {
    RmUnknownApp = 0,
    RmMainWindow = 1,
    RmOtherWindow = 2,
    RmService = 3,
    RmExplorer = 4,
    RmConsole = 5,
    RmCritical = 1000
  }

  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct RM_PROCESS_INFO {
    public RM_UNIQUE_PROCESS Process;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=CCH_RM_MAX_APP_NAME + 1)]
    public string strAppName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=CCH_RM_MAX_SVC_NAME + 1)]
    public string strServiceShortName;
    public RM_APP_TYPE ApplicationType;
    public uint AppStatus;
    public uint TSSessionId;
    [MarshalAs(UnmanagedType.Bool)]
    public bool bRestartable;
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct MIB_TCPROW_OWNER_PID {
    public uint dwState;
    public uint dwLocalAddr;
    public uint dwLocalPort;
    public uint dwRemoteAddr;
    public uint dwRemotePort;
    public uint dwOwningPid;
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct UNICODE_STRING {
    public ushort Length;
    public ushort MaximumLength;
    public IntPtr Buffer;
  }

  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool Process32FirstW(IntPtr snapshot, ref PROCESSENTRY32 entry);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool Process32NextW(IntPtr snapshot, ref PROCESSENTRY32 entry);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern IntPtr OpenProcess(uint access, bool inheritHandle, int processId);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
  [DllImport("kernel32.dll", SetLastError=true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool QueryFullProcessImageName(IntPtr handle, int flags, StringBuilder path, ref int size);
  [DllImport("kernel32.dll", SetLastError=true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool GetProcessTimes(IntPtr handle, out long creation, out long exit, out long kernel, out long user);
  [DllImport("kernel32.dll", SetLastError=true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool TerminateProcess(IntPtr handle, uint exitCode);
  [DllImport("kernel32.dll", SetLastError=true)]
  [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool CloseHandle(IntPtr handle);
  [DllImport("user32.dll")]
  [return: MarshalAs(UnmanagedType.Bool)]
  static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern int GetWindowTextW(IntPtr window, StringBuilder text, int maxCount);
  [DllImport("user32.dll", SetLastError=true)]
  static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

  [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)]
  static extern int RmStartSession(out uint handle, int flags, StringBuilder key);
  [DllImport("rstrtmgr.dll", CharSet=CharSet.Unicode)]
  static extern int RmRegisterResources(
    uint handle,
    uint fileCount,
    string[] files,
    uint appCount,
    RM_UNIQUE_PROCESS[] apps,
    uint serviceCount,
    string[] services
  );
  [DllImport("rstrtmgr.dll")]
  static extern int RmGetList(
    uint handle,
    out uint needed,
    ref uint count,
    [In, Out] RM_PROCESS_INFO[] info,
    ref uint reasons
  );
  [DllImport("rstrtmgr.dll")]
  static extern int RmShutdown(uint handle, uint actionFlags, IntPtr statusCallback);
  [DllImport("rstrtmgr.dll")]
  static extern int RmEndSession(uint handle);
  [DllImport("iphlpapi.dll", SetLastError=true)]
  static extern uint GetExtendedTcpTable(
    IntPtr table,
    ref int size,
    [MarshalAs(UnmanagedType.Bool)] bool order,
    int ipVersion,
    int tableClass,
    uint reserved
  );
  [DllImport("ntdll.dll")]
  static extern int NtQueryInformationProcess(
    IntPtr processHandle,
    int informationClass,
    IntPtr information,
    int informationLength,
    out int returnLength
  );

  public static int[] GetLockOwners(string path) {
    uint handle;
    var key = new StringBuilder(CCH_RM_SESSION_KEY + 1);
    int result = RmStartSession(out handle, 0, key);
    if (result != 0) {
      throw new InvalidOperationException("RmStartSession=" + result);
    }
    try {
      result = RmRegisterResources(handle, 1, new[] { path }, 0, null, 0, null);
      if (result != 0) {
        throw new InvalidOperationException("RmRegisterResources=" + result);
      }
      uint needed = 0;
      uint count = 0;
      uint reasons = 0;
      result = RmGetList(handle, out needed, ref count, null, ref reasons);
      if (result == 0) return new int[0];
      if (result != ERROR_MORE_DATA) {
        throw new InvalidOperationException("RmGetList(size)=" + result);
      }
      var info = new RM_PROCESS_INFO[needed];
      count = needed;
      result = RmGetList(handle, out needed, ref count, info, ref reasons);
      if (result != 0) {
        throw new InvalidOperationException("RmGetList(data)=" + result);
      }
      var ids = new List<int>();
      for (int i = 0; i < count; i++) {
        ids.Add(info[i].Process.dwProcessId);
      }
      return ids.ToArray();
    } finally {
      RmEndSession(handle);
    }
  }

  public static int ShutdownExactProcess(int processId) {
    if (processId <= 0) return 87;
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000;
    IntPtr processHandle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, processId);
    if (processHandle == IntPtr.Zero) return Marshal.GetLastWin32Error();

    long creation = 0;
    long exit = 0;
    long kernel = 0;
    long user = 0;
    try {
      if (!GetProcessTimes(processHandle, out creation, out exit, out kernel, out user)) {
        return Marshal.GetLastWin32Error();
      }
    } finally {
      CloseHandle(processHandle);
    }

    var startTime = new System.Runtime.InteropServices.ComTypes.FILETIME {
      dwLowDateTime = unchecked((int)(creation & 0xffffffffL)),
      dwHighDateTime = unchecked((int)(creation >> 32))
    };
    var process = new RM_UNIQUE_PROCESS {
      dwProcessId = processId,
      ProcessStartTime = startTime
    };

    uint sessionHandle;
    var key = new StringBuilder(CCH_RM_SESSION_KEY + 1);
    int result = RmStartSession(out sessionHandle, 0, key);
    if (result != 0) return result;
    try {
      result = RmRegisterResources(
        sessionHandle,
        0,
        null,
        1,
        new[] { process },
        0,
        null
      );
      if (result != 0) return result;

      uint needed = 0;
      uint count = 0;
      uint reasons = 0;
      result = RmGetList(sessionHandle, out needed, ref count, null, ref reasons);
      if (result != ERROR_MORE_DATA || needed != 1) return result == 0 ? 1168 : result;
      var info = new RM_PROCESS_INFO[needed];
      count = needed;
      result = RmGetList(sessionHandle, out needed, ref count, info, ref reasons);
      if (result != 0 || count != 1) return result == 0 ? 1168 : result;
      var observed = info[0].Process;
      if (
        observed.dwProcessId != processId ||
        observed.ProcessStartTime.dwLowDateTime != startTime.dwLowDateTime ||
        observed.ProcessStartTime.dwHighDateTime != startTime.dwHighDateTime
      ) {
        return 1168;
      }

      return RmShutdown(sessionHandle, 0x1, IntPtr.Zero);
    } finally {
      RmEndSession(sessionHandle);
    }
  }

  public static int[] GetTcpListenerOwners(int port) {
    if (port <= 0 || port > 65535) return new int[0];
    const int AF_INET = 2;
    const int TCP_TABLE_OWNER_PID_LISTENER = 3;
    const uint ERROR_INSUFFICIENT_BUFFER = 122;

    int size = 0;
    uint result = GetExtendedTcpTable(
      IntPtr.Zero,
      ref size,
      false,
      AF_INET,
      TCP_TABLE_OWNER_PID_LISTENER,
      0
    );
    if (result != ERROR_INSUFFICIENT_BUFFER || size <= 4) {
      return new int[0];
    }

    IntPtr buffer = Marshal.AllocHGlobal(size);
    try {
      result = GetExtendedTcpTable(
        buffer,
        ref size,
        false,
        AF_INET,
        TCP_TABLE_OWNER_PID_LISTENER,
        0
      );
      if (result != 0) return new int[0];

      int count = Marshal.ReadInt32(buffer);
      int rowSize = Marshal.SizeOf(typeof(MIB_TCPROW_OWNER_PID));
      IntPtr rowPointer = IntPtr.Add(buffer, sizeof(int));
      var owners = new HashSet<int>();
      for (int i = 0; i < count; i++) {
        var row = (MIB_TCPROW_OWNER_PID)Marshal.PtrToStructure(
          rowPointer,
          typeof(MIB_TCPROW_OWNER_PID)
        );
        int localPort = (ushort)IPAddress.NetworkToHostOrder(
          unchecked((short)(row.dwLocalPort & 0xffff))
        );
        if (localPort == port && row.dwOwningPid > 0) {
          owners.Add(unchecked((int)row.dwOwningPid));
        }
        rowPointer = IntPtr.Add(rowPointer, rowSize);
      }
      var resultIds = new int[owners.Count];
      owners.CopyTo(resultIds);
      return resultIds;
    } finally {
      Marshal.FreeHGlobal(buffer);
    }
  }

  public static int[] GetProcessIdsByExactWindowTitle(string expectedTitle) {
    var owners = new HashSet<int>();
    if (String.IsNullOrEmpty(expectedTitle)) return new int[0];
    EnumWindowsProc callback = delegate(IntPtr window, IntPtr parameter) {
      var title = new StringBuilder(1024);
      if (GetWindowTextW(window, title, title.Capacity) > 0 &&
          String.Equals(title.ToString(), expectedTitle, StringComparison.Ordinal)) {
        uint processId;
        GetWindowThreadProcessId(window, out processId);
        if (processId > 0) owners.Add(unchecked((int)processId));
      }
      return true;
    };
    EnumWindows(callback, IntPtr.Zero);
    GC.KeepAlive(callback);
    var resultIds = new int[owners.Count];
    owners.CopyTo(resultIds);
    return resultIds;
  }

  public static string GetProcessCommandLine(int processId) {
    if (processId <= 0) return null;
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000;
    const int ProcessCommandLineInformation = 60;
    IntPtr processHandle = OpenProcess(
      PROCESS_QUERY_LIMITED_INFORMATION,
      false,
      processId
    );
    if (processHandle == IntPtr.Zero) return null;
    try {
      int required = 0;
      NtQueryInformationProcess(
        processHandle,
        ProcessCommandLineInformation,
        IntPtr.Zero,
        0,
        out required
      );
      if (required <= Marshal.SizeOf(typeof(UNICODE_STRING))) return null;
      IntPtr buffer = Marshal.AllocHGlobal(required);
      try {
        int returned;
        int status = NtQueryInformationProcess(
          processHandle,
          ProcessCommandLineInformation,
          buffer,
          required,
          out returned
        );
        if (status != 0) return null;
        var value = (UNICODE_STRING)Marshal.PtrToStructure(
          buffer,
          typeof(UNICODE_STRING)
        );
        if (value.Buffer == IntPtr.Zero || value.Length == 0) return String.Empty;
        return Marshal.PtrToStringUni(value.Buffer, value.Length / 2);
      } finally {
        Marshal.FreeHGlobal(buffer);
      }
    } finally {
      CloseHandle(processHandle);
    }
  }
}
"@
}

function Test-ProcessIdAliveFast([int]$ProcessId) {
  Initialize-HomeStackNativeProcess
  if ($ProcessId -le 0) { return $false }
  $handle = [HomeStackNativeProcess]::OpenProcess(0x00100000, $false, $ProcessId)
  if ($handle -eq [IntPtr]::Zero) { return $false }
  try {
    # WAIT_TIMEOUT means the process has not signalled its exit handle.
    return ([HomeStackNativeProcess]::WaitForSingleObject($handle, 0) -eq 0x00000102)
  } finally {
    [HomeStackNativeProcess]::CloseHandle($handle) | Out-Null
  }
}

function Get-ProcessIdsByExecutableNameFast([string]$ExecutableName) {
  Initialize-HomeStackNativeProcess
  $ids = @()
  if (-not $ExecutableName) { return $ids }
  $target = [System.IO.Path]::GetFileName($ExecutableName).ToLowerInvariant()
  if (-not $target.EndsWith(".exe")) { $target += ".exe" }
  $snapshot = [HomeStackNativeProcess]::CreateToolhelp32Snapshot(
    [HomeStackNativeProcess]::TH32CS_SNAPPROCESS,
    0
  )
  if ($snapshot -eq [IntPtr]::Zero -or $snapshot -eq [IntPtr](-1)) { return $ids }
  try {
    $entry = New-Object "HomeStackNativeProcess+PROCESSENTRY32"
    $entry.dwSize = [Runtime.InteropServices.Marshal]::SizeOf(
      [type]"HomeStackNativeProcess+PROCESSENTRY32"
    )
    $ok = [HomeStackNativeProcess]::Process32FirstW($snapshot, [ref]$entry)
    while ($ok) {
      if ($entry.szExeFile -and $entry.szExeFile.ToLowerInvariant() -eq $target) {
        $ids += [int]$entry.th32ProcessID
      }
      $ok = [HomeStackNativeProcess]::Process32NextW($snapshot, [ref]$entry)
    }
  } finally {
    [HomeStackNativeProcess]::CloseHandle($snapshot) | Out-Null
  }
  return $ids
}

function Get-ProcessIdsByExactWindowTitleFast([string]$WindowTitle) {
  Initialize-HomeStackNativeProcess
  if (-not $WindowTitle) { return @() }
  try {
    return @([HomeStackNativeProcess]::GetProcessIdsByExactWindowTitle($WindowTitle))
  } catch {
    return @()
  }
}

function Get-ProcessCommandLineFast([int]$ProcessId) {
  Initialize-HomeStackNativeProcess
  if ($ProcessId -le 0) { return $null }
  try {
    return [HomeStackNativeProcess]::GetProcessCommandLine($ProcessId)
  } catch {
    return $null
  }
}

function Get-FileLockOwnerProcessIdsFast([string]$Path) {
  Initialize-HomeStackNativeProcess
  if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return @() }
  try {
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    return @([HomeStackNativeProcess]::GetLockOwners($resolved))
  } catch {
    return @()
  }
}

function Stop-ExactProcessViaRestartManagerFast([int]$ProcessId) {
  Initialize-HomeStackNativeProcess
  if ($ProcessId -le 0) { return $false }
  try {
    return ([HomeStackNativeProcess]::ShutdownExactProcess($ProcessId) -eq 0)
  } catch {
    return $false
  }
}

function Test-ExecutableRunningFast([string]$ExecutableName) {
  return @((Get-ProcessIdsByExecutableNameFast $ExecutableName)).Count -gt 0
}

function Get-ProcessExecutableNameFast([int]$ProcessId) {
  Initialize-HomeStackNativeProcess
  if ($ProcessId -le 0) { return $null }
  $handle = [HomeStackNativeProcess]::OpenProcess(0x00101000, $false, $ProcessId)
  if ($handle -eq [IntPtr]::Zero) { return $null }
  try {
    $size = 32768
    $path = New-Object System.Text.StringBuilder $size
    if (-not [HomeStackNativeProcess]::QueryFullProcessImageName($handle, 0, $path, [ref]$size)) {
      return $null
    }
    return [System.IO.Path]::GetFileNameWithoutExtension($path.ToString()).ToLowerInvariant()
  } finally {
    [HomeStackNativeProcess]::CloseHandle($handle) | Out-Null
  }
}

function Get-ProcessStartTimeUtcFast([int]$ProcessId) {
  Initialize-HomeStackNativeProcess
  if ($ProcessId -le 0) { return $null }
  $handle = [HomeStackNativeProcess]::OpenProcess(0x00101000, $false, $ProcessId)
  if ($handle -eq [IntPtr]::Zero) { return $null }
  try {
    [long]$created = 0
    [long]$exited = 0
    [long]$kernel = 0
    [long]$user = 0
    if (-not [HomeStackNativeProcess]::GetProcessTimes(
      $handle, [ref]$created, [ref]$exited, [ref]$kernel, [ref]$user
    )) {
      return $null
    }
    return [datetime]::FromFileTimeUtc($created)
  } finally {
    [HomeStackNativeProcess]::CloseHandle($handle) | Out-Null
  }
}

function Stop-ProcessIdFast([int]$ProcessId) {
  Initialize-HomeStackNativeProcess
  if ($ProcessId -le 0) { return $false }
  $handle = [HomeStackNativeProcess]::OpenProcess(0x00100001, $false, $ProcessId)
  if ($handle -eq [IntPtr]::Zero) { return $false }
  try {
    return [HomeStackNativeProcess]::TerminateProcess($handle, 1)
  } finally {
    [HomeStackNativeProcess]::CloseHandle($handle) | Out-Null
  }
}

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $iar = $c.BeginConnect("127.0.0.1", $P, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(1200)) {
      $c.Close()
      return $false
    }
    $c.EndConnect($iar)
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

# HTTP liveness check — confirms the server actually answers (not just that the port
# is bound). Replaces the 400ms TCP-only check that produced false "offline" flicker
# on the Agent Hub when the listening socket was momentarily slow under load.
function Test-HttpAlive([string]$Url, [int]$TimeoutMs = 1500) {
  try {
    $code = curl.exe -s --max-time ([math]::Max(1, [int]($TimeoutMs / 1000))) -o NUL -w "%{http_code}" $Url 2>$null
    return ([int]$code -ge 200 -and [int]$code -lt 500)
  } catch {
    return $false
  }
}

<# Probe several URLs in parallel with one cancellation-bounded HttpClient so the
bridge listener never blocks for sum(url timeouts). Returns hashtable url -> bool. #>
function Test-HttpAliveParallel([string[]]$Urls, [int]$TimeoutMs = 1500) {
  $out = @{}
  try { Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue } catch { }
  $handler = New-Object System.Net.Http.HttpClientHandler
  $handler.UseProxy = $false
  $client = New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout = [TimeSpan]::FromMilliseconds([math]::Max(250, $TimeoutMs))
  $tasks = @{}
  try {
    foreach ($u in $Urls) {
      $out[$u] = $false
      try { $tasks[$u] = $client.GetAsync($u) } catch { }
    }
    $deadline = [datetime]::UtcNow.AddMilliseconds($TimeoutMs)
    while (
      @($tasks.Values | Where-Object { -not $_.IsCompleted }).Count -gt 0 -and
      [datetime]::UtcNow -lt $deadline
    ) {
      Start-Sleep -Milliseconds 20
    }
    foreach ($u in $tasks.Keys) {
      $task = $tasks[$u]
      if (-not $task.IsCompleted -or $task.IsCanceled -or $task.IsFaulted) { continue }
      try {
        $resp = $task.GetAwaiter().GetResult()
        $code = [int]$resp.StatusCode
        $out[$u] = ($code -ge 200 -and $code -lt 500)
        $resp.Dispose()
      } catch { }
    }
  } finally {
    # This is non-blocking and releases any unresolved DNS/TLS/socket work
    # without calling HttpWebRequest.Abort on the serialized listener thread.
    try { $client.CancelPendingRequests() } catch { }
    $client.Dispose()
    $handler.Dispose()
  }
  return $out
}

function Test-MultiPortOpen([int[]]$Ports, [int]$TimeoutMs = 400) {
  $pending = @{}
  foreach ($p in $Ports) {
    try {
      $c = New-Object System.Net.Sockets.TcpClient
      $iar = $c.BeginConnect("127.0.0.1", $p, $null, $null)
      $pending[$p] = @{ Client = $c; Ar = $iar }
    } catch { }
  }
  Start-Sleep -Milliseconds $TimeoutMs
  $out = @{}
  foreach ($p in $Ports) {
    $out[$p] = $false
    if (-not $pending.ContainsKey($p)) { continue }
    $entry = $pending[$p]
    if ($entry.Ar.IsCompleted) {
      try {
        $entry.Client.EndConnect($entry.Ar)
        $out[$p] = $true
      } catch { }
    }
    $entry.Client.Close()
  }
  return $out
}

function Test-TunnelLive([string]$Url) {
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri "$Url/api/ping" -UseBasicParsing -TimeoutSec 4
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

$script:TunnelLiveCache = @{ url = ""; live = $false; at = [datetime]::MinValue }
function Test-TunnelLiveCached([string]$Url) {
  if (-not $Url) { return $false }
  $now = Get-Date
  if ($script:TunnelLiveCache.url -eq $Url -and ($now - $script:TunnelLiveCache.at).TotalSeconds -lt 12) {
    return $script:TunnelLiveCache.live
  }
  $live = Test-TunnelLive $Url
  $script:TunnelLiveCache = @{ url = $Url; live = $live; at = $now }
  return $live
}

# F4c-429 (2026-07-08 incident) — Smart tunnel reachability check that
# distinguishes 429 (rate-limited at Cloudflare edge) from real outage.
#
# The flap loop: bridge-watchdog and supervisor were both probing
# https://bot.doxxedcrypto.digital/api/ping every 60s. Combined with the
# Railway bot-bridge cache poll (every 2-5s) and the user's browser, we
# blew through Cloudflare's free-tier tunnel rate limit (~1000 req/min,
# enforced per source IP). 429 then cascaded:
#   supervisor: 429 -> Test-HttpOk false -> 5 ticks -> RECOVER tunnel
#   bridge-watchdog: 429 -> Test-TunnelUp false -> /cmd/start-tunnel
# Each restart cost ~3s downtime and made the rate limit worse (re-registration
# hits the edge again). Tunnel flapped every 5-10 min for 2+ hours.
#
# Fix: 429 means "tunnel is FINE, you're asking too often." Treat it as
# healthy AND signal the caller to back off via the shared $script:TunnelBackoff
# state below. Only 5xx / connection errors / timeouts count as "dead."
function Test-TunnelHttpSmart {
  param(
    [string]$Url,
    [int]$TimeoutSec = 6,
    [string]$UserAgent = "dcf-tunnel-probe/1.0"
  )
  $result = @{ Healthy = $false; RateLimited = $false; StatusCode = 0; Error = $null }
  if (-not $Url) { $result.Error = "no-url"; return $result }
  $pingUrl = "$Url".TrimEnd('/') + "/api/ping"
  $handler = $null
  $client = $null
  try {
    try { Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue } catch { }
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds([math]::Max(1, $TimeoutSec))
    $client.DefaultRequestHeaders.UserAgent.ParseAdd($UserAgent)
    $task = $client.GetAsync($pingUrl)
    $deadline = [datetime]::UtcNow.AddSeconds([math]::Max(1, $TimeoutSec))
    while (-not $task.IsCompleted -and [datetime]::UtcNow -lt $deadline) {
      Start-Sleep -Milliseconds 20
    }
    if (-not $task.IsCompleted) {
      $result.Error = "Timeout"
    } elseif ($task.IsCanceled) {
      $result.Error = "Timeout"
    } elseif ($task.IsFaulted) {
      $result.Error = "RequestFailed"
    } else {
      $resp = $task.GetAwaiter().GetResult()
      $code = [int]$resp.StatusCode
      $result.StatusCode = $code
      $resp.Dispose()
      if ($code -ge 200 -and $code -lt 400) {
        $result.Healthy = $true
      } elseif ($code -eq 429) {
        $result.Healthy = $true
        $result.RateLimited = $true
      } else {
        $result.Error = "http-$code"
      }
    }
  } catch {
    $result.Error = $_.Exception.Message
  } finally {
    try { if ($client) { $client.CancelPendingRequests() } } catch { }
    if ($client) { $client.Dispose() }
    if ($handler) { $handler.Dispose() }
  }
  return $result
}

function Test-TunnelConnectorPresent([object]$Probe) {
  if (-not $Probe) { return $false }
  if ([bool]$Probe.Healthy) { return $true }
  $code = [int]$Probe.StatusCode
  # A 4xx response, or Cloudflare's origin-side 502/504, proves that the
  # connector/route answered even when the local bot origin is still down.
  return (($code -ge 200 -and $code -lt 500) -or $code -in @(502, 504))
}

# Shared cross-process backoff state. Both bridge-watchdog.ps1 and
# home-stack-supervisor.ps1 dot-source this file, so the $script: scope
# gives each its own copy. That's fine — what we actually want is for a
# rate-limit signal from the smart probe to (a) suppress the NEXT probe
# from the same poller for a few minutes, and (b) NOT trigger a RECOVER.
# Set-TunnelBackoff records "we just saw a 429"; Test-TunnelBackoffActive
# returns true if we're still inside the cool-down so the caller can skip
# the network probe entirely (and just trust the last reading).
$script:TunnelBackoff = @{ until = [datetime]::MinValue; count = 0; lastAt = [datetime]::MinValue }
function Set-TunnelBackoff {
  param([int]$Seconds = 180)
  $now = Get-Date
  $script:TunnelBackoff.until = $now.AddSeconds($Seconds)
  $script:TunnelBackoff.count = [int]$script:TunnelBackoff.count + 1
  $script:TunnelBackoff.lastAt = $now
}
function Test-TunnelBackoffActive {
  return ((Get-Date) -lt $script:TunnelBackoff.until)
}
function Get-TunnelBackoffState {
  return $script:TunnelBackoff
}

function Get-ListenPortOwners([int]$ListenPort) {
  Initialize-HomeStackNativeProcess
  if ($ListenPort -le 0 -or $ListenPort -gt 65535) { return @() }
  try {
    return @([HomeStackNativeProcess]::GetTcpListenerOwners($ListenPort))
  } catch {
    return @()
  }
}

function Test-PortBound([int]$ListenPort) {
  return @(Get-ListenPortOwners $ListenPort).Count -gt 0
}

function Stop-ListenPortFast([int]$ListenPort) {
  $killed = @()
  # Most recoveries have no listener. Avoid the Windows TCP table entirely in
  # that common case; it is known to block for minutes on a degraded host.
  if (-not (Test-PortOpen $ListenPort)) { return $killed }
  @(Get-ListenPortOwners $ListenPort) | ForEach-Object {
    $procId = [int]$_
    if ($procId -gt 0 -and $procId -ne 4 -and $killed -notcontains $procId) {
      $stopped = Stop-ProcessIdFast $procId
      if (-not $stopped) {
        $stopped = Stop-ExactProcessViaRestartManagerFast $procId
      }
      $deadline = (Get-Date).AddSeconds(5)
      while ((Test-ProcessIdAliveFast $procId) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 100
      }
      if (Test-ProcessIdAliveFast $procId) {
        Write-Warning "Unable to stop listener PID $procId on port $ListenPort."
      } else {
        $killed += $procId
      }
    }
  }
  return $killed
}

function Stop-RecordedProcess(
  [string]$PidFile,
  [string[]]$AllowedNames = @(),
  [int]$MaxStartSkewMinutes = 5
) {
  $killed = @()
  if (-not (Test-Path -LiteralPath $PidFile)) { return $killed }
  try {
    $recordedPid = [int]((Get-Content -LiteralPath $PidFile -Raw -ErrorAction Stop).Trim())
    if (Test-ProcessIdAliveFast $recordedPid) {
      $processName = Get-ProcessExecutableNameFast $recordedPid
      $normalizedAllowed = @($AllowedNames | ForEach-Object { "$_".ToLowerInvariant() })
      if ($normalizedAllowed.Count -gt 0 -and $normalizedAllowed -notcontains $processName) {
        return $killed
      }
      $stamp = (Get-Item -LiteralPath $PidFile).LastWriteTimeUtc
      $started = Get-ProcessStartTimeUtcFast $recordedPid
      # A valid owner starts immediately before its PID file is written.  The
      # skew guard prevents a stale PID file from killing an unrelated process
      # after Windows has reused the numeric PID.
      if ($started -and [math]::Abs(($stamp - $started).TotalMinutes) -le $MaxStartSkewMinutes) {
        Stop-ProcessIdFast $recordedPid | Out-Null
        $killed += $recordedPid
      }
    }
  } catch { }
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  return $killed
}

function Stop-RelayStatePusher {
  $killed = @()
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine -like "*relay-state-pusher.ps1*"
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  return $killed
}

function Stop-HomeStackSupervisor {
  $killed = @()
  $killed += @(Stop-RecordedProcess (Join-Path $repoRoot ".home-stack-supervisor.pid") @("powershell", "pwsh", "cmd"))
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -like "*home-stack-supervisor.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*"
      )
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.pid") -Force -ErrorAction SilentlyContinue
  try {
    if (Test-Path (Join-Path $repoRoot ".home-stack-supervisor.lock")) {
      Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue
    }
  } catch { }
  return $killed
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  if ($ProcessId -le 0) { return }
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ParentProcessId -eq $ProcessId } |
    ForEach-Object { Stop-ProcessTree $_.ProcessId }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Close-WindowsByTitlePrefix {
  param(
    [string[]]$Prefixes,
    [int[]]$ExcludeProcessIds = @()
  )
  $closed = @()
  $exclude = @{}
  foreach ($id in $ExcludeProcessIds) {
    if ($id -gt 0) { $exclude[$id] = $true }
  }
  Get-Process cmd, powershell, pwsh -ErrorAction SilentlyContinue | ForEach-Object {
    if ($exclude.ContainsKey($_.Id)) { return }
    $t = $_.MainWindowTitle
    if (-not $t) { return }
    foreach ($prefix in $Prefixes) {
      if ($t -like "$prefix*") {
        Stop-ProcessTree $_.Id
        $closed += "title:$t"
        return
      }
    }
  }
  return $closed
}

function Close-StaleOrchestratorConsoles {
  param([int[]]$ExcludeProcessIds = @())
  # Start/Stop orchestration consoles deliberately spawn the long-lived bot,
  # analyzer, bridge and tunnel processes.  Closing the whole process tree here
  # therefore kills healthy services several minutes after a successful start.
  # Close only the stale orchestrator shell; its detached component children
  # must remain alive and continue to be supervised independently.
  $closed = @()
  $exclude = @{}
  foreach ($id in $ExcludeProcessIds) {
    if ($id -gt 0) { $exclude[$id] = $true }
  }
  Get-Process cmd, powershell, pwsh -ErrorAction SilentlyContinue | ForEach-Object {
    if ($exclude.ContainsKey($_.Id)) { return }
    $title = $_.MainWindowTitle
    if (-not $title) { return }
    if ($title -like "Doxed Start Everything*" -or $title -like "Doxed Stop Everything*") {
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
      $closed += "title:$title"
    }
  }
  return @($closed)
}

function Invoke-HomeTerminalHygiene {
  param(
    [int]$BotPort = 7002,
    [int]$AnalyzerPort = 9500
  )
  $closed = [System.Collections.Generic.List[string]]::new()
  foreach ($t in (Close-StaleOrchestratorConsoles)) { $closed.Add($t) }

  $dupPushers = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*relay-state-pusher.ps1*" })
  if ($dupPushers.Count -gt 1) {
    $dupPushers | Sort-Object CreationDate -Descending | Select-Object -Skip 1 | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $closed.Add("relay-pusher-dup:$($_.ProcessId)")
    }
  }

  $dupSupervisors = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*home-stack-supervisor.ps1*" })
  if ($dupSupervisors.Count -gt 1) {
    $dupSupervisors | Sort-Object CreationDate -Descending | Select-Object -Skip 1 | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $closed.Add("supervisor-dup:$($_.ProcessId)")
    }
  }

  foreach ($t in (Close-WindowsByTitlePrefix @(
    "Doxed Wire to Site",
    "Doxed Auto-Wire"
  ))) {
    if ($closed -notcontains $t) { $closed.Add($t) }
  }

  return @($closed)
}

function Close-ShowcaseStackConsoles {
  param(
    [int]$GlobalBotPort = 7002,
    [int]$GlobalAnalyzerPort = 9500,
    [switch]$KeepBridge,
    [int[]]$ExcludeProcessIds = @()
  )
  $closed = @()
  $exclude = @{}
  foreach ($id in $ExcludeProcessIds) {
    if ($id -gt 0) { $exclude[$id] = $true }
  }

  $titlePrefixes = @(
    "Doxed Bot :$GlobalBotPort",
    "Doxed Analyzer :$GlobalAnalyzerPort",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Start Everything",
    "Doxed Stack Start",
    "Doxed Stop Everything"
  )
  if (-not $KeepBridge) {
    $titlePrefixes += "Doxed Home Bridge"
    $titlePrefixes += "TEST Bridge"
  }

  Get-Process cmd, powershell, pwsh -ErrorAction SilentlyContinue | ForEach-Object {
    if ($exclude.ContainsKey($_.Id)) { return }
    $t = $_.MainWindowTitle
    if (-not $t) { return }
    foreach ($prefix in $titlePrefixes) {
      if ($t -like "$prefix*") {
        Stop-ProcessTree $_.Id
        $closed += "title:$t"
        return
      }
    }
  }

  $scriptNeedles = @(
    "start-home-bot.ps1",
    "start-home-analyzer.ps1",
    "restart-home-tunnel.ps1",
    "home-stack-start-everything.ps1",
    "home-stack-start-all.ps1"
  )
  if (-not $KeepBridge) {
    $scriptNeedles += @("ensure-home-bridge.ps1", "home-stack-launcher.ps1")
  }

  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      if ($exclude.ContainsKey($_.ProcessId)) { return $false }
      if (-not $_.CommandLine) { return $false }
      foreach ($needle in $scriptNeedles) {
        if ($_.CommandLine -like "*$needle*") { return $true }
      }
      return $false
    } | ForEach-Object {
      Stop-ProcessTree $_.ProcessId
      $closed += "pid:$($_.ProcessId)"
    }

  return $closed
}

function Stop-ListenPort([int]$ListenPort) {
  return @(Stop-ListenPortFast $ListenPort)
}

function Stop-PythonMatching([string]$Pattern) {
  $killed = @()
  Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Pattern*" } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  return $killed
}

function Stop-Cloudflared {
  $killed = @()
  $killed += @(Stop-RecordedProcess $cloudflaredPidFile @("cloudflared"))
  foreach ($processId in @(Get-ProcessIdsByExecutableNameFast "cloudflared")) {
    if ($processId -le 0) { continue }
    if (Stop-ProcessIdFast $processId) {
      $killed += $processId
    }
  }
  Remove-Item -LiteralPath $cloudflaredPidFile -Force -ErrorAction SilentlyContinue
  return $killed
}

function Test-HomeScriptRunning([string]$ScriptName) {
  $hit = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ScriptName*" } |
    Select-Object -First 1
  return [bool]$hit
}

function Close-HomeStackWindows {
  $closed = @()
  $windowTitles = @(
    "Doxed Bot :7002",
    "Doxed Analyzer",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Cloudflare Tunnel (stable)",
    "Doxed Stack Control Panel",
    "Doxed Auto-Wire",
    "Doxed Wire to Site",
    "Doxed Tunnel Watchdog",
    "Doxed Tunnel Restart",
    "Doxed Stack Start"
  )
  foreach ($title in $windowTitles) {
    & taskkill.exe /F /FI "WINDOWTITLE eq $title" 2>$null | Out-Null
    $closed += $title
  }
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -like "*start-home-bot.ps1*" -or
        $_.CommandLine -like "*start-home-analyzer.ps1*" -or
        $_.CommandLine -like "*setup-home-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*run-named-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*restart-home-tunnel.ps1*" -or
        $_.CommandLine -like "*home-stack-control-panel.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*" -or
        $_.CommandLine -like "*wire-home-bot-background.ps1*" -or
        $_.CommandLine -like "*tunnel-watchdog.ps1*" -or
        $_.CommandLine -like "*home-stack-start-all.ps1*" -or
        $_.CommandLine -like "*relay-state-pusher.ps1*"
      )
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $closed += "pid:$($_.ProcessId)"
    }
  return $closed
}

function Clear-TunnelUrlFile {
  if (Test-Path $tunnelUrlFile) {
    Set-Content -Path $tunnelUrlFile -Value "" -NoNewline
  }
}

function Close-HomeStackWindowTitles {
  $closed = @()
  $windowTitles = @(
    "Doxed Bot :7002",
    "Doxed Analyzer",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Cloudflare Tunnel (stable)",
    "Doxed Stack Control Panel",
    "Doxed Auto-Wire",
    "Doxed Wire to Site",
    "Doxed Tunnel Watchdog",
    "Doxed Tunnel Restart",
    "Doxed Stack Start",
    "Doxed Start Everything",
    "Local Collection Bot :7002",
    "Local Collection Analyzer :9500",
    "Local Collection Analyzer (once)"
  )
  foreach ($title in $windowTitles) {
    & taskkill.exe /F /FI "WINDOWTITLE eq $title" 2>$null | Out-Null
    $closed += $title
  }
  return $closed
}

function Stop-BotPidFile {
  $killed = @()
  $killed += @(Stop-RecordedProcess (Join-Path $repoRoot ".home-bot-crash-monitor.pid") @("powershell", "pwsh", "cmd"))
  $pidFile = Join-Path $repoRoot ".home-bot.pid"
  if (-not (Test-Path $pidFile)) { return $killed }
  try {
    $raw = Get-Content $pidFile -Raw -ErrorAction SilentlyContinue
    $botPid = [int]"$raw".Trim()
    if ($botPid -gt 0) {
      Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
      $killed += $botPid
    }
  } catch { }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  return $killed
}

function Stop-GlobalStackFast {
  param(
    [int]$GlobalBotPort = 7002,
    [int]$GlobalAnalyzerPort = 9500,
    [int[]]$ExcludeProcessIds = @()
  )
  # Stop supervisor first so it cannot restart bot/analyzer/tunnel during shutdown.
  Set-HomeStackUserStopped
  $supervisor = @(Stop-HomeStackSupervisor)
  $relayPusher = @(Stop-RelayStatePusher)
  # Kill port listeners + hidden detached bot before tunnel/other cleanup (NoWait uses Hidden python).
  $botPidFile = @(Stop-BotPidFile)
  $botPort = @(Stop-ListenPortFast $GlobalBotPort)
  $botPy = @(Stop-PythonMatching "btc_conservative_agent")
  $analyzerPort = @(Stop-ListenPortFast $GlobalAnalyzerPort)
  $analyzerPy = @(Stop-PythonMatching "analyzer_research_engine")
  $tunnel = @(Stop-Cloudflared)
  Start-Sleep -Seconds 1
  $botPort += @(Stop-ListenPortFast $GlobalBotPort)
  $botPy += @(Stop-PythonMatching "btc_conservative_agent")
  $analyzerPort += @(Stop-ListenPortFast $GlobalAnalyzerPort)
  $analyzerPy += @(Stop-PythonMatching "analyzer_research_engine")
  $consoles = @(Close-ShowcaseStackConsoles -GlobalBotPort $GlobalBotPort -GlobalAnalyzerPort $GlobalAnalyzerPort -KeepBridge -ExcludeProcessIds $ExcludeProcessIds)
  Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  Clear-TunnelUrlFile
  return @{
    botPort = @($botPort | Select-Object -Unique)
    analyzerPort = @($analyzerPort | Select-Object -Unique)
    tunnel = $tunnel
    relayPusher = $relayPusher
    supervisor = $supervisor
    botPidFile = $botPidFile
    pythonBot = @($botPy | Select-Object -Unique)
    pythonAnalyzer = @($analyzerPy | Select-Object -Unique)
    consoles = $consoles
  }
}

function Stop-LocalLabFast {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $repoRoot = Split-Path -Parent $scriptDir
  $stopScript = Join-Path (Split-Path -Parent $repoRoot) "stop_stack.ps1"
  if (Test-Path $stopScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript 2>$null | Out-Null
  } else {
    Stop-ListenPortFast 7002 | Out-Null
    Stop-ListenPortFast 9500 | Out-Null
    Stop-PythonMatching "15minu_bot.py" | Out-Null
  }
  return @{ stopped = $true; ports = @(7002, 9500) }
}

function Stop-AllHomeStackFast {
  # Legacy: stop global showcase ports only (does not touch local lab :7002/:9500).
  return Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort
}

function Stop-AllHomeStack {
  $result = Stop-AllHomeStackFast
  $scriptHits = @()
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -like "*start-home-bot.ps1*" -or
        $_.CommandLine -like "*start-home-analyzer.ps1*" -or
        $_.CommandLine -like "*setup-home-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*run-named-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*restart-home-tunnel.ps1*" -or
        $_.CommandLine -like "*home-stack-control-panel.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*" -or
        $_.CommandLine -like "*wire-home-bot-background.ps1*" -or
        $_.CommandLine -like "*tunnel-watchdog.ps1*" -or
        $_.CommandLine -like "*home-stack-start-all.ps1*" -or
        $_.CommandLine -like "*relay-state-pusher.ps1*"
      )
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $scriptHits += "pid:$($_.ProcessId)"
    }
  $result.scriptProcesses = $scriptHits
  return $result
}

function Test-AnalyzerRunning {
  if (Test-PortOpen $AnalyzerPort) { return $true }
  $hit = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*analyzer_research_engine*" } |
    Select-Object -First 1
  return [bool]$hit
}

function Test-BotRunning {
  if (Test-PortOpen $BotPort) { return $true }
  $hit = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*btc_conservative_agent*" } |
    Select-Object -First 1
  return [bool]$hit
}

function Start-HiddenPs1 {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @()
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $scriptEsc = ($ScriptPath -replace '"', '""')
  $psArgs = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptEsc`""
  foreach ($a in $ExtraArgs) {
    if ($null -eq $a -or "$a" -eq "") { continue }
    $aEsc = ("$a" -replace '"', '""')
    if ($aEsc -match '\s') { $psArgs += " `"$aEsc`"" } else { $psArgs += " $aEsc" }
  }
  # PowerShell's Start-Process throws when the host environment contains both
  # `Path` and `PATH` and standard-error redirection asks it to clone that
  # dictionary. Use the native .NET launcher directly: one hidden PowerShell,
  # no intermediate cmd.exe, no inherited pipe that can die with the caller.
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = (Get-Command powershell.exe -ErrorAction Stop).Source
  $startInfo.Arguments = $psArgs
  $startInfo.WorkingDirectory = $repoRoot
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $startInfo
  if (-not $process.Start()) { throw "Failed to launch hidden script: $ScriptPath" }
  $process.Dispose()
}

function Start-VisibleConsole {
  # F9 (2026-07-07 follow-up) --plugged the orphan cmd.exe leak identified by
  # the tidy-shells verifier. Previous version always used cmd /k + pause>nul
  # for non -NoPause callers (which is the default), so every supervisor
  # recovery spawned a cmd window that sat at "Press any key" forever because
  # no human was watching. Result: 14+ orphan cmd.exe processes, ~71 stale
  # launcher .cmd files in logs/launchers, growing at ~1/hour.
  #
  # Fix:
  #   - Default mode is now self-closing (cmd /c, no pause). Safe for
  #     unattended supervisor recovery paths.
  #   - New -Wait switch opts INTO the old behavior (visible window with
  #     "Press any key" prompt) for first-run / interactive use.
  #   - -NoPause still respected as alias for the new default (back-compat).
  #   - Old callers that pass -NoPause explicitly still work; old callers
  #     that relied on the pause prompt should switch to -Wait if they want
  #     a human to see the output.
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [string]$Title = "Doxed Home Stack",
    [switch]$NoPause,
    [switch]$Wait
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $launcherDir = Join-Path $repoRoot "logs\launchers"
  if (-not (Test-Path $launcherDir)) {
    New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
  }
  $argLine = ""
  foreach ($a in $ExtraArgs) {
    if ($null -eq $a -or "$a" -eq "") { continue }
    if ("$a" -match '\s') { $argLine += " `"$a`"" } else { $argLine += " $a" }
  }
  # Periodically prune stale launcher .cmd files so logs/launchers/ doesn't
  # grow unbounded across weeks of supervisor restarts. Anything older than
  # 1 day is safe to remove - the launcher has either run or failed by then.
  try {
    Get-ChildItem -Path $launcherDir -Filter "run-*.cmd" -ErrorAction SilentlyContinue |
      Where-Object { $_.CreationTime -lt (Get-Date).AddDays(-1) } |
      Remove-Item -Force -ErrorAction SilentlyContinue
  } catch { }

  $launcher = Join-Path $launcherDir ("run-" + [guid]::NewGuid().ToString("n") + ".cmd")
  $titleSafe = ($Title -replace '"', '')
  $scriptSafe = $ScriptPath
  $repoSafe = $repoRoot
  $wantPause = $Wait -and -not $NoPause
  $launcherLines = @(
    "@echo off",
    "title `"$titleSafe`"",
    "cd /d `"$repoSafe`"",
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptSafe`"$argLine",
    "if errorlevel 1 echo [ERROR] Script exited with code %errorlevel%"
  )
  if ($wantPause) {
    $launcherLines += @(
      "echo.",
      "echo --- Press any key to close this window ---",
      "pause >nul"
    )
  }
  $launcherLines | Set-Content -Path $launcher -Encoding ASCII
  # /c = exit when script exits (default, no leak).
  # /k = keep open after script exits (only when -Wait requested, pauses).
  $cmdMode = if ($wantPause) { "/k" } else { "/c" }
  # Pure cmd.exe window - avoids cmd parsing bugs with :7002 in title.
  $windowStyle = if ($wantPause) { "Normal" } else { "Minimized" }
  Start-Process -FilePath "cmd.exe" -ArgumentList @($cmdMode, "`"$launcher`"") `
    -WorkingDirectory $repoRoot -WindowStyle $windowStyle
}


function Start-HomeTunnel {
  param(
    [int]$Port = 0,
    [switch]$Force,
    [switch]$PreferVisible
  )
  if ($Port -le 0) { $Port = $BotPort }
  $restartScript = Join-Path $scriptDir "restart-home-tunnel.ps1"
  if (-not (Test-Path $restartScript)) { throw "Missing script: $restartScript" }
  $doForce = [bool]$Force
  if ((Use-NamedTunnel) -and -not $PreferVisible) {
    & $restartScript -Port $Port -Force:$doForce -Hidden | Out-Null
    return
  }
  if ($PreferVisible) {
    Start-VisibleConsole -ScriptPath $restartScript -ExtraArgs @("-Port", "$Port", "-Force") -Title "Doxed Cloudflare Tunnel" -NoPause
    return
  }
  Start-HiddenPs1 -ScriptPath $restartScript -ExtraArgs @("-Port", "$Port", "-Force", "-Hidden")
}

function Start-DetachedPs1 {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [switch]$NoExit,
    [string]$WindowTitle = "Doxed Home Stack",
    [ValidateSet("Minimized", "Normal")]
    [string]$Show = "Normal"
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  if ($Show -eq "Normal") {
    Start-VisibleConsole -ScriptPath $ScriptPath -ExtraArgs $ExtraArgs -Title $WindowTitle
    return
  }
  $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $ExtraArgs
  if ($NoExit) { $argList = @("-NoExit") + $argList }
  Start-Process -FilePath "powershell.exe" -ArgumentList $argList -WorkingDirectory $repoRoot -WindowStyle Minimized
}

function Get-TunnelUrl {
  if (Test-Path $tunnelUrlFile) {
    $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
    if ($null -ne $raw -and "$raw".Trim()) {
      $t = "$raw".Trim()
      if ($t -match 'bot\.doxxedcrypto\.digital' -and -not (Use-NamedTunnel)) {
        return $null
      }
      return $t
    }
  }
  return $null
}

function Test-NamedTunnelConfigured {
  $configDir = Join-Path $env:USERPROFILE ".cloudflared"
  $cred = Get-ChildItem -Path (Join-Path $configDir "doxed-btc-bot*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
  $token = Join-Path $configDir "doxed-btc-bot.token"
  return ($null -ne $cred) -or (Test-Path $token)
}

function Use-NamedTunnel {
  if (-not (Test-NamedTunnelConfigured)) { return $false }
  $flag = Join-Path $repoRoot ".home-use-named-tunnel"
  if (Test-Path $flag) { return $true }
  $showcaseLock = Join-Path $repoRoot "config\home-showcase.lock.json"
  if (Test-Path $showcaseLock) {
    try {
      $lock = Get-Content $showcaseLock -Raw | ConvertFrom-Json
      if ($lock.frozen -and -not [bool]$lock.disableTunnel) { return $true }
    } catch { }
  }
  return $false
}

function Start-AnalyzerDashboard {
  return (Test-PortOpen $AnalyzerPort)
}

function Start-CloudflaredNamedHidden {
  # F4 (2026-07-07 incident follow-up) — default --protocol to http2 because
  # this network blocks UDP/7844 to region2.v2.argotunnel.com, which produces
  # QUIC retry storms and silent 4h outages. The bridge spawn path here is
  # the one actually used in production (not run-named-bot-tunnel.ps1) —
  # closing the gap the Cloudflare tunnel investigator flagged.
  # Default port is 7002 (canonical showcase per config/bot-architecture.lock.json).
  param(
    [int]$Port = 7002,
    [string]$Protocol = $(if ($env:CLOUDFLARED_PROTOCOL) { $env:CLOUDFLARED_PROTOCOL } else { 'http2' })
  )
  $configDir = Join-Path $env:USERPROFILE ".cloudflared"
  $tunnelName = "doxed-btc-bot"
  $tokenFile = Join-Path $configDir "$tunnelName.token"
  $logDir = Join-Path $repoRoot "logs"
  if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
  $outLog = Join-Path $logDir "cloudflared-named.log"
  $errLog = Join-Path $logDir "cloudflared-named.err.log"

  if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared not installed"
  }

  Stop-Cloudflared | Out-Null
  Start-Sleep -Seconds 2

  # This Windows host has no reliable outbound IPv6/QUIC path, and the current
  # cloudflared connectivity precheck can stall before it emits a log or opens a
  # socket. Use the host-proven IPv4/HTTP2 path, disable cloudflared's redundant
  # updater (Windows does not auto-update it anyway), and skip only that startup
  # precheck. The live connector still retries and fails closed if the edge is
  # actually unreachable. Set CLOUDFLARED_PROTOCOL=auto to revert the protocol.
  $args = @(
    "tunnel",
    "--no-autoupdate",
    "--no-prechecks",
    "--edge-ip-version", "4",
    "--protocol", $Protocol,
    "run"
  )
  if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    $args += @("--token", $token)
  } else {
    $cred = Get-ChildItem -Path (Join-Path $configDir "$tunnelName*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cred) { throw "Named tunnel not configured" }
    $configPath = Join-Path $configDir "config.yml"
    @(
      "tunnel: $tunnelName"
      "credentials-file: $($cred.FullName)"
      "ingress:"
      "  - hostname: bot.doxxedcrypto.digital"
      "    service: http://127.0.0.1:$Port"
      "  - service: http_status:404"
    ) | Set-Content -Path $configPath -Encoding UTF8
    $args += $tunnelName
  }

  Set-Content -Path $tunnelUrlFile -Value "https://bot.doxxedcrypto.digital" -NoNewline
  foreach ($rotLog in @($outLog, $errLog)) {
    try {
      if ((Get-Item $rotLog -ErrorAction SilentlyContinue).Length -gt 1048571) {
        $tail = Get-Content $rotLog -Tail 200 -ErrorAction SilentlyContinue
        if ($tail) { $tail | Set-Content $rotLog -Encoding UTF8 }
      }
    } catch { }
  }

  # Some launchers (including the desktop automation host) inherit both
  # `Path` and `PATH`. Windows treats those names as the same variable, but
  # Start-Process builds a case-insensitive environment dictionary and throws
  # before cloudflared can start when both spellings are present. Canonicalize
  # the process-local copy before spawning the detached connector.
  $pathKeys = @(
    [Environment]::GetEnvironmentVariables().Keys |
      Where-Object { "$_" -ieq "Path" }
  )
  if ($pathKeys.Count -gt 1) {
    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    foreach ($pathKey in $pathKeys) {
      [Environment]::SetEnvironmentVariable([string]$pathKey, $null, "Process")
    }
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
  }

  $cloudflared = Start-Process -FilePath "cloudflared" -ArgumentList $args -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WorkingDirectory $repoRoot -PassThru
  if (-not $cloudflared) { throw "cloudflared failed to launch" }
  Set-Content -LiteralPath $cloudflaredPidFile -Value $cloudflared.Id -NoNewline
}
