import { contextBridge, ipcRenderer } from 'electron';

export type PairDefaults = {
  apiBaseUrl: string;
  label: string;
};

export type PairInput = {
  apiBaseUrl: string;
  code: string;
  label: string;
};

contextBridge.exposeInMainWorld('founderNodePair', {
  getDefaults: (): Promise<PairDefaults> => ipcRenderer.invoke('get-pair-defaults'),
  openSettings: (): Promise<void> => ipcRenderer.invoke('open-settings'),
  pair: (input: PairInput): Promise<void> => ipcRenderer.invoke('pair', input),
});
