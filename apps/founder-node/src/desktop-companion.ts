import { app, BrowserWindow, ipcMain, screen } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

export type DesktopCompanionState = 'idle' | 'working' | 'success' | 'attention' | 'error';

export interface DesktopCompanionSnapshot {
  visible: boolean;
  state: DesktopCompanionState;
  title: string;
  detail: string;
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
};

let companionWindow: BrowserWindow | null = null;
let lastSnapshot = DEFAULT_SNAPSHOT;
let ipcRegistered = false;
let hitTestTimer: NodeJS.Timeout | null = null;
let mouseInteractive = false;
let dragOffset: { x: number; y: number } | null = null;

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
  let candidate = defaultPosition();
  try {
    const parsed = JSON.parse(fs.readFileSync(positionFile(), 'utf8')) as {
      x?: number;
      y?: number;
    };
    if (Number.isFinite(parsed.x) && Number.isFinite(parsed.y)) {
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
    const { x, y } = companionWindow.getBounds();
    fs.mkdirSync(path.dirname(positionFile()), { recursive: true });
    fs.writeFileSync(positionFile(), JSON.stringify({ x, y }, null, 2), 'utf8');
  } catch {
    // Position persistence is optional; the companion still works without it.
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
    persistPosition();
  });
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

export function updateDesktopCompanion(snapshot: DesktopCompanionSnapshot): void {
  lastSnapshot = {
    visible: snapshot.visible,
    state: snapshot.state,
    title: snapshot.title.slice(0, 96),
    detail: snapshot.detail.slice(0, 220),
  };
  if (!companionWindow || companionWindow.isDestroyed()) createDesktopCompanion();
  if (!lastSnapshot.visible) {
    setMouseInteractive(false);
    companionWindow?.hide();
    return;
  }
  companionWindow?.showInactive();
  configureWindowInteraction();
  sendSnapshot();
}

export function destroyDesktopCompanion(): void {
  persistPosition();
  stopCursorHitTesting();
  dragOffset = null;
  if (companionWindow && !companionWindow.isDestroyed()) companionWindow.destroy();
  companionWindow = null;
}
