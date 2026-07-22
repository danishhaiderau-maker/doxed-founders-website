import { app, BrowserWindow, ipcMain, Menu, screen } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

export type DesktopCompanionState =
  | 'idle'
  | 'listening'
  | 'planning'
  | 'working'
  | 'coordinating'
  | 'verifying'
  | 'success'
  | 'attention'
  | 'error'
  | 'offline'
  | 'update';

export type DesktopCompanionAction =
  | 'openTask'
  | 'openUsage'
  | 'openSettings'
  | 'hide'
  | 'toggleReducedMotion'
  | 'signOut';

export interface DesktopCompanionSnapshot {
  visible: boolean;
  state: DesktopCompanionState;
  title: string;
  detail: string;
  reducedMotion: boolean;
}

const WINDOW_WIDTH = 360;
const WINDOW_HEIGHT = 300;
const POSITION_FILE = 'founder-companion-window.json';
const HIT_TEST_INTERVAL_MS = 50;
const DRAGON_HIT_AREA = { left: 126, top: 86, right: 336, bottom: 296 };
const BUBBLE_HIT_AREA = { left: 8, top: 8, right: 352, bottom: 88 };

const DEFAULT_SNAPSHOT: DesktopCompanionSnapshot = {
  visible: true,
  state: 'idle',
  title: 'Resting in the nest',
  detail: 'Founder is ready for the next mission.',
  reducedMotion: false,
};

let companionWindow: BrowserWindow | null = null;
let lastSnapshot = DEFAULT_SNAPSHOT;
let lastTaskSnapshot = DEFAULT_SNAPSHOT;
let pendingUpdateSnapshot: DesktopCompanionSnapshot | null = null;
let ipcRegistered = false;
let hitTestTimer: NodeJS.Timeout | null = null;
let mouseInteractive = false;
let dragOffset: { x: number; y: number } | null = null;
let actionHandler: ((action: DesktopCompanionAction) => void) | null = null;
let hiddenUntilNextTask = false;

interface StoredCompanionPositions {
  lastDisplayId?: string;
  positions?: Record<string, { x: number; y: number }>;
  /** Legacy single-position format kept for a safe migration. */
  x?: number;
  y?: number;
}

function assetRoot(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'companion')
    : path.join(__dirname, '..', 'companion-assets');
}

function positionFile(): string {
  return path.join(app.getPath('userData'), POSITION_FILE);
}

function defaultPosition(): { x: number; y: number } {
  const workArea = screen.getPrimaryDisplay().workArea;
  return {
    x: workArea.x + workArea.width - WINDOW_WIDTH - 24,
    y: workArea.y + workArea.height - WINDOW_HEIGHT - 24,
  };
}

function restoredPosition(): { x: number; y: number } {
  const displays = screen.getAllDisplays();
  let candidate = defaultPosition();
  try {
    const parsed = JSON.parse(fs.readFileSync(positionFile(), 'utf8')) as StoredCompanionPositions;
    const selectedDisplay = displays.find(display => String(display.id) === parsed.lastDisplayId);
    const perDisplay = selectedDisplay && parsed.positions?.[String(selectedDisplay.id)];
    if (perDisplay && Number.isFinite(perDisplay.x) && Number.isFinite(perDisplay.y)) {
      candidate = { x: Math.round(perDisplay.x), y: Math.round(perDisplay.y) };
    } else if (Number.isFinite(parsed.x) && Number.isFinite(parsed.y)) {
      candidate = { x: Math.round(parsed.x!), y: Math.round(parsed.y!) };
    }
  } catch {
    // First launch or a stale position file. Use the lower-right default.
  }

  const display = screen.getDisplayMatching({
    x: candidate.x,
    y: candidate.y,
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
  });
  const bounds = display.workArea;
  return {
    x: Math.min(Math.max(candidate.x, bounds.x), bounds.x + bounds.width - WINDOW_WIDTH),
    y: Math.min(Math.max(candidate.y, bounds.y), bounds.y + bounds.height - WINDOW_HEIGHT),
  };
}

function persistPosition(): void {
  if (!companionWindow || companionWindow.isDestroyed()) return;
  try {
    const bounds = companionWindow.getBounds();
    const display = screen.getDisplayMatching(bounds);
    let stored: StoredCompanionPositions = {};
    try {
      stored = JSON.parse(fs.readFileSync(positionFile(), 'utf8')) as StoredCompanionPositions;
    } catch {
      // The first persisted position creates the file below.
    }
    const positions = stored.positions ?? {};
    positions[String(display.id)] = { x: bounds.x, y: bounds.y };
    fs.mkdirSync(path.dirname(positionFile()), { recursive: true });
    fs.writeFileSync(positionFile(), JSON.stringify({
      lastDisplayId: String(display.id),
      positions,
    } satisfies StoredCompanionPositions, null, 2), 'utf8');
  } catch {
    // Position persistence is optional; the companion still works without it.
  }
}

function snapToNearbyEdge(): void {
  if (!companionWindow || companionWindow.isDestroyed()) return;
  const bounds = companionWindow.getBounds();
  const workArea = screen.getDisplayMatching(bounds).workArea;
  const candidates = [
    { distance: Math.abs(bounds.x - workArea.x), x: workArea.x, y: bounds.y },
    {
      distance: Math.abs(bounds.x + bounds.width - (workArea.x + workArea.width)),
      x: workArea.x + workArea.width - bounds.width,
      y: bounds.y,
    },
    { distance: Math.abs(bounds.y - workArea.y), x: bounds.x, y: workArea.y },
    {
      distance: Math.abs(bounds.y + bounds.height - (workArea.y + workArea.height)),
      x: bounds.x,
      y: workArea.y + workArea.height - bounds.height,
    },
  ].sort((left, right) => left.distance - right.distance);
  const nearest = candidates[0];
  if (nearest && nearest.distance <= 36) {
    companionWindow.setPosition(Math.round(nearest.x), Math.round(nearest.y));
  }
}

function sendSnapshot(): void {
  if (!companionWindow || companionWindow.isDestroyed() || companionWindow.webContents.isLoading()) return;
  companionWindow.webContents.send('founder-companion-state', lastSnapshot);
}

function setMouseInteractive(interactive: boolean): void {
  if (!companionWindow || companionWindow.isDestroyed() || mouseInteractive === interactive) return;
  mouseInteractive = interactive;
  companionWindow.setIgnoreMouseEvents(!interactive, { forward: true });
}

function isInside(
  x: number,
  y: number,
  area: { left: number; top: number; right: number; bottom: number },
): boolean {
  return x >= area.left && x <= area.right && y >= area.top && y <= area.bottom;
}

function updateCursorHitTest(): void {
  if (!companionWindow || companionWindow.isDestroyed() || !lastSnapshot.visible) {
    setMouseInteractive(false);
    return;
  }
  const cursor = screen.getCursorScreenPoint();
  const bounds = companionWindow.getBounds();
  const localX = cursor.x - bounds.x;
  const localY = cursor.y - bounds.y;
  const overDragon = isInside(localX, localY, DRAGON_HIT_AREA);
  const overBubble = lastSnapshot.state !== 'idle' && isInside(localX, localY, BUBBLE_HIT_AREA);
  setMouseInteractive(overDragon || overBubble);
}

function startCursorHitTesting(): void {
  if (hitTestTimer) return;
  hitTestTimer = setInterval(updateCursorHitTest, HIT_TEST_INTERVAL_MS);
  hitTestTimer.unref();
}

function applyShapedHitArea(): void {
  if (!companionWindow || companionWindow.isDestroyed()) return;
  const areas = [DRAGON_HIT_AREA];
  if (lastSnapshot.state !== 'idle') areas.unshift(BUBBLE_HIT_AREA);
  companionWindow.setShape(
    areas.map((area) => ({
      x: area.left,
      y: area.top,
      width: area.right - area.left,
      height: area.bottom - area.top,
    })),
  );
  setMouseInteractive(true);
}

function configureWindowInteraction(): void {
  if (process.platform === 'win32' || process.platform === 'linux') {
    applyShapedHitArea();
    return;
  }
  startCursorHitTesting();
}

function stopCursorHitTesting(): void {
  if (hitTestTimer) clearInterval(hitTestTimer);
  hitTestTimer = null;
  mouseInteractive = false;
}

function registerIpc(): void {
  if (ipcRegistered) return;
  ipcRegistered = true;
  ipcMain.on('founder-companion-ready', (event) => {
    if (event.sender === companionWindow?.webContents) sendSnapshot();
  });
  ipcMain.on('founder-companion-drag-start', (event) => {
    if (event.sender !== companionWindow?.webContents || !companionWindow) return;
    const cursor = screen.getCursorScreenPoint();
    const bounds = companionWindow.getBounds();
    dragOffset = { x: cursor.x - bounds.x, y: cursor.y - bounds.y };
  });
  ipcMain.on('founder-companion-drag-move', (event) => {
    if (event.sender !== companionWindow?.webContents || !companionWindow || !dragOffset) return;
    const cursor = screen.getCursorScreenPoint();
    companionWindow.setPosition(
      Math.round(cursor.x - dragOffset.x),
      Math.round(cursor.y - dragOffset.y),
    );
  });
  ipcMain.on('founder-companion-drag-end', (event) => {
    if (event.sender !== companionWindow?.webContents) return;
    dragOffset = null;
    snapToNearbyEdge();
    persistPosition();
  });
  ipcMain.on('founder-companion-open-task', (event) => {
    if (event.sender === companionWindow?.webContents) actionHandler?.('openTask');
  });
  ipcMain.on('founder-companion-show-menu', (event) => {
    if (event.sender !== companionWindow?.webContents || !companionWindow) return;
    const send = (action: DesktopCompanionAction) => actionHandler?.(action);
    Menu.buildFromTemplate([
      { label: 'Open current task', click: () => send('openTask') },
      { label: 'Plan and usage', click: () => send('openUsage') },
      { label: 'Founder Settings', click: () => send('openSettings') },
      { type: 'separator' },
      {
        label: 'Hide until next task',
        click: () => {
          hiddenUntilNextTask = true;
          setMouseInteractive(false);
          companionWindow?.hide();
        },
      },
      {
        label: 'Reduce motion',
        type: 'checkbox',
        checked: lastSnapshot.reducedMotion,
        click: () => send('toggleReducedMotion'),
      },
      { label: 'Hide Dragon', click: () => send('hide') },
      { label: 'Sign out', click: () => send('signOut') },
    ]).popup({ window: companionWindow });
  });
}

export function setDesktopCompanionActionHandler(
  handler: ((action: DesktopCompanionAction) => void) | null,
): void {
  actionHandler = handler;
}

export function createDesktopCompanion(): BrowserWindow {
  if (companionWindow && !companionWindow.isDestroyed()) return companionWindow;
  registerIpc();
  const { x, y } = restoredPosition();
  companionWindow = new BrowserWindow({
    x,
    y,
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: app.isPackaged
        ? path.join(process.resourcesPath, 'companion', 'companion-preload.js')
        : path.join(__dirname, 'companion-preload.js'),
    },
  });
  companionWindow.setAlwaysOnTop(true, 'floating');
  companionWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  companionWindow.setIgnoreMouseEvents(true, { forward: true });
  configureWindowInteraction();
  companionWindow.on('moved', persistPosition);
  companionWindow.on('closed', () => {
    stopCursorHitTesting();
    dragOffset = null;
    companionWindow = null;
  });
  companionWindow.webContents.on('did-finish-load', () => {
    sendSnapshot();
    if (lastSnapshot.visible) companionWindow?.showInactive();
  });
  void companionWindow.loadFile(path.join(assetRoot(), 'companion.html'));
  return companionWindow;
}

function renderDesktopCompanion(snapshot: DesktopCompanionSnapshot): void {
  lastSnapshot = {
    visible: snapshot.visible,
    state: snapshot.state,
    title: snapshot.title.slice(0, 96),
    detail: snapshot.detail.slice(0, 220),
    reducedMotion: snapshot.reducedMotion,
  };
  if (hiddenUntilNextTask && snapshot.state !== 'idle' && snapshot.state !== 'offline') {
    hiddenUntilNextTask = false;
  }
  if (!companionWindow || companionWindow.isDestroyed()) createDesktopCompanion();
  if (!lastSnapshot.visible || hiddenUntilNextTask) {
    setMouseInteractive(false);
    companionWindow?.hide();
    return;
  }
  companionWindow?.showInactive();
  configureWindowInteraction();
  sendSnapshot();
}

export function updateDesktopCompanion(snapshot: DesktopCompanionSnapshot): void {
  lastTaskSnapshot = snapshot;
  const canShowUpdate = snapshot.state === 'idle' || snapshot.state === 'update';
  renderDesktopCompanion(canShowUpdate && pendingUpdateSnapshot
    ? pendingUpdateSnapshot
    : snapshot);
}

export function updateDesktopCompanionUpdate(
  snapshot: DesktopCompanionSnapshot | null,
): void {
  pendingUpdateSnapshot = snapshot;
  if (lastTaskSnapshot.state !== 'idle' && lastTaskSnapshot.state !== 'update') return;
  renderDesktopCompanion(snapshot ?? lastTaskSnapshot);
}

export function destroyDesktopCompanion(): void {
  persistPosition();
  stopCursorHitTesting();
  dragOffset = null;
  if (companionWindow && !companionWindow.isDestroyed()) companionWindow.destroy();
  companionWindow = null;
  actionHandler = null;
  hiddenUntilNextTask = false;
  lastSnapshot = DEFAULT_SNAPSHOT;
  lastTaskSnapshot = DEFAULT_SNAPSHOT;
  pendingUpdateSnapshot = null;
}
