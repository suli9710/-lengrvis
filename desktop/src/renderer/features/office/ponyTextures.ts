import coffeeAnimated from "../../assets/xiaoma-agent/fc_drink_coffee.gif";
import coffeeStill from "../../assets/xiaoma-agent/fc_drink_coffee_still.png?no-inline";
import restroomAnimated from "../../assets/xiaoma-agent/fc_pooping-0_cropped.gif";
import restroomStill from "../../assets/xiaoma-agent/fc_pooping-0_cropped_still.png?no-inline";
import walkAnimated from "../../assets/xiaoma-agent/fc_walking_h.gif";
import walkStill from "../../assets/xiaoma-agent/fc_walking_h_still.png?no-inline";
import treadmillAnimated from "../../assets/xiaoma-agent/running_treadmill_cropped.gif";
import treadmillStill from "../../assets/xiaoma-agent/running_treadmill_cropped_still.png?no-inline";
import saluteAnimated from "../../assets/xiaoma-agent/salute.gif";
import saluteStill from "../../assets/xiaoma-agent/salute_still.png?no-inline";
import napAnimated from "../../assets/xiaoma-agent/sleeping.gif";
import napStill from "../../assets/xiaoma-agent/sleeping_still.png?no-inline";
import idleAnimated from "../../assets/xiaoma-agent/standby.gif";
import idleStill from "../../assets/xiaoma-agent/standby_still.png?no-inline";
import workingAnimated from "../../assets/xiaoma-agent/working.gif";
import workingStill from "../../assets/xiaoma-agent/working_still.png?no-inline";
import type { OfficeFacing, PonyClip } from "./ponyMotion";

export type PonyTextureMode = "animated" | "still";

export interface PonyTextureAsset {
  id: string;
  animatedSrc: string;
  stillSrc: string;
  sourceWidth: number;
  sourceHeight: number;
  groundX: number;
  groundY: number;
  scale: number;
  nativeFacing: OfficeFacing;
}

export interface PonyTextureLayout {
  leftPercent: number;
  topPercent: number;
  widthPercent: number;
  heightPercent: number;
}

export interface ResolvedPonyTexture {
  clip: PonyClip;
  asset: PonyTextureAsset;
  mode: PonyTextureMode;
  src: string;
  key: string;
}

const baseStageWidth = 150;
const baseStageHeight = 124;

const idleTexture: PonyTextureAsset = {
  id: "idle",
  animatedSrc: idleAnimated,
  stillSrc: idleStill,
  sourceWidth: 267,
  sourceHeight: 200,
  groundX: 136,
  groundY: 101,
  scale: 0.93,
  nativeFacing: "left"
};

const walkTexture: PonyTextureAsset = {
  id: "walk",
  animatedSrc: walkAnimated,
  stillSrc: walkStill,
  sourceWidth: 267,
  sourceHeight: 200,
  groundX: 135,
  groundY: 166,
  scale: 0.75,
  nativeFacing: "left"
};

const workingTexture: PonyTextureAsset = {
  id: "working",
  animatedSrc: workingAnimated,
  stillSrc: workingStill,
  sourceWidth: 267,
  sourceHeight: 200,
  groundX: 135,
  groundY: 101,
  scale: 0.95,
  nativeFacing: "left"
};

const coffeeTexture: PonyTextureAsset = {
  id: "coffee",
  animatedSrc: coffeeAnimated,
  stillSrc: coffeeStill,
  sourceWidth: 267,
  sourceHeight: 200,
  groundX: 132,
  groundY: 113,
  scale: 0.82,
  nativeFacing: "left"
};

const treadmillTexture: PonyTextureAsset = {
  id: "treadmill",
  animatedSrc: treadmillAnimated,
  stillSrc: treadmillStill,
  sourceWidth: 196,
  sourceHeight: 129,
  groundX: 98,
  groundY: 125,
  scale: 0.66,
  nativeFacing: "left"
};

const napTexture: PonyTextureAsset = {
  id: "nap",
  animatedSrc: napAnimated,
  stillSrc: napStill,
  sourceWidth: 267,
  sourceHeight: 200,
  groundX: 134,
  groundY: 118,
  scale: 0.86,
  nativeFacing: "left"
};

const saluteTexture: PonyTextureAsset = {
  id: "salute",
  animatedSrc: saluteAnimated,
  stillSrc: saluteStill,
  sourceWidth: 267,
  sourceHeight: 200,
  groundX: 132,
  groundY: 115,
  scale: 0.9,
  nativeFacing: "left"
};

const restroomTexture: PonyTextureAsset = {
  id: "restroom",
  animatedSrc: restroomAnimated,
  stillSrc: restroomStill,
  sourceWidth: 157,
  sourceHeight: 217,
  groundX: 79,
  groundY: 217,
  scale: 0.62,
  nativeFacing: "right"
};

export const ponyTextureManifest: Record<PonyClip, PonyTextureAsset> = {
  idle: idleTexture,
  walk: walkTexture,
  working: workingTexture,
  phone: saluteTexture,
  coffee: coffeeTexture,
  treadmill: treadmillTexture,
  nap: napTexture,
  salute: saluteTexture,
  restroom: restroomTexture
};

export function resolvePonyTexture(clip: PonyClip, animated: boolean): ResolvedPonyTexture {
  const asset = ponyTextureManifest[clip];
  const mode: PonyTextureMode = animated ? "animated" : "still";
  return createResolvedTexture(clip, asset, mode);
}

export function ponyTextureLayout(asset: PonyTextureAsset): PonyTextureLayout {
  return {
    leftPercent: 50 - (asset.groundX * asset.scale / baseStageWidth) * 100,
    topPercent: 100 - (asset.groundY * asset.scale / baseStageHeight) * 100,
    widthPercent: (asset.sourceWidth * asset.scale / baseStageWidth) * 100,
    heightPercent: (asset.sourceHeight * asset.scale / baseStageHeight) * 100
  };
}

export function ponyTextureCandidates(texture: ResolvedPonyTexture) {
  const candidates = [texture];
  if (texture.mode === "animated") {
    candidates.push(createResolvedTexture(texture.clip, texture.asset, "still"));
  }
  candidates.push(createResolvedTexture(texture.clip, idleTexture, "still"));
  return candidates.filter((candidate, index) =>
    candidates.findIndex((item) => item.src === candidate.src) === index
  );
}

export function nextPonyTextureFallback(texture: ResolvedPonyTexture) {
  if (texture.mode === "animated") {
    return createResolvedTexture(texture.clip, texture.asset, "still");
  }
  if (texture.asset !== idleTexture) {
    return createResolvedTexture(texture.clip, idleTexture, "still");
  }
  return null;
}

function createResolvedTexture(
  clip: PonyClip,
  asset: PonyTextureAsset,
  mode: PonyTextureMode
): ResolvedPonyTexture {
  return {
    clip,
    asset,
    mode,
    src: mode === "animated" ? asset.animatedSrc : asset.stillSrc,
    key: `${asset.id}:${mode}`
  };
}
