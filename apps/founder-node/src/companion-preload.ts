import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('founderCompanion', {
  ready: (): void => ipcRenderer.send('founder-companion-ready'),
  beginDrag: (): void => ipcRenderer.send('founder-companion-drag-start'),
  dragToPointer: (): void => ipcRenderer.send('founder-companion-drag-move'),
  endDrag: (): void => ipcRenderer.send('founder-companion-drag-end'),
  onState: (listener: (snapshot: unknown) => void): void => {
    ipcRenderer.on('founder-companion-state', (_event, snapshot) => listener(snapshot));
  },
});
