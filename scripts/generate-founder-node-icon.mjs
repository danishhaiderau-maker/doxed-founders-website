#!/usr/bin/env node
/**
 * Generates Founder Node tray/app icons (PNG + ICO for Windows).
 * electron-builder generates .icns from PNG on macOS builds.
 */
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const buildDir = path.join(__dirname, '../apps/founder-node/build');

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii');
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const crcBuf = Buffer.alloc(4);
  const crc = crc32(Buffer.concat([typeBuf, data]));
  crcBuf.writeUInt32BE(crc);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

function createPng(width, height, pixelFn) {
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    const rowStart = y * (width * 4 + 1);
    raw[rowStart] = 0;
    for (let x = 0; x < width; x++) {
      const [r, g, b, a] = pixelFn(x, y, width, height);
      const i = rowStart + 1 + x * 4;
      raw[i] = r;
      raw[i + 1] = g;
      raw[i + 2] = b;
      raw[i + 3] = a;
    }
  }

  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  const idat = zlib.deflateSync(raw, { level: 9 });

  return Buffer.concat([
    signature,
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', idat),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function founderNodePixel(x, y, w, h) {
  const cx = w / 2;
  const cy = h / 2;
  const dx = (x - cx) / (w / 2);
  const dy = (y - cy) / (h / 2);
  const dist = Math.sqrt(dx * dx + dy * dy);

  if (dist > 0.92) return [0, 0, 0, 0];

  const t = (x + y) / (w + h);
  const r = Math.round(8 + t * 20);
  const g = Math.round(120 + t * 60);
  const b = Math.round(180 + (1 - t) * 40);

  const inner = dist < 0.55;
  if (inner) {
    const letter =
      inLetterF(x, y, w, h) || inLetterN(x, y, w, h) ? [255, 255, 255, 255] : [r, g, b, 255];
    return letter;
  }

  return [r, g, b, 255];
}

function inLetterF(x, y, w, h) {
  const sx = w * 0.28;
  const sy = h * 0.28;
  const fw = w * 0.14;
  const fh = h * 0.44;
  const bar = w * 0.22;
  if (x >= sx && x < sx + fw && y >= sy && y < sy + fh) return true;
  if (x >= sx && x < sx + bar && y >= sy && y < sy + fh * 0.22) return true;
  if (x >= sx && x < sx + bar * 0.85 && y >= sy + fh * 0.38 && y < sy + fh * 0.52) return true;
  return false;
}

function inLetterN(x, y, w, h) {
  const sx = w * 0.56;
  const sy = h * 0.28;
  const nw = w * 0.14;
  const nh = h * 0.44;
  if (x >= sx && x < sx + nw && y >= sy && y < sy + nh) return true;
  if (x >= sx + nw * 2.1 && x < sx + nw * 3.1 && y >= sy && y < sy + nh) return true;
  const relX = (x - sx) / (nw * 3.1);
  const relY = (y - sy) / nh;
  if (relX >= 0 && relX <= 1 && relY >= 0 && relY <= 1) {
    if (Math.abs(relY - relX) < 0.12) return true;
  }
  return false;
}

function writeIco(png512Path, icoPath) {
  const png256 = createPng(256, 256, founderNodePixel);
  const png48 = createPng(48, 48, founderNodePixel);
  const png32 = createPng(32, 32, founderNodePixel);
  const png16 = createPng(16, 16, founderNodePixel);
  const images = [png256, png48, png32, png16];

  const count = images.length;
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(count, 4);

  let offset = 6 + count * 16;
  const entries = [];
  const dataParts = [];

  for (const img of images) {
    const size = img.readUInt32BE(16);
    const entry = Buffer.alloc(16);
    entry[0] = size >= 256 ? 0 : size;
    entry[1] = size >= 256 ? 0 : size;
    entry[2] = 0;
    entry[3] = 0;
    entry[4] = 1;
    entry[5] = 0;
    entry[6] = 32;
    entry[7] = 0;
    entry.writeUInt32LE(img.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    dataParts.push(img);
    offset += img.length;
  }

  fs.writeFileSync(icoPath, Buffer.concat([header, ...entries, ...dataParts]));
  fs.copyFileSync(png512Path, path.join(buildDir, 'icon.png'));
}

fs.mkdirSync(buildDir, { recursive: true });

const png512 = createPng(512, 512, founderNodePixel);
const pngPath = path.join(buildDir, 'icon.png');
fs.writeFileSync(pngPath, png512);
writeIco(pngPath, path.join(buildDir, 'icon.ico'));

console.log('Founder Node icons written to apps/founder-node/build/');
