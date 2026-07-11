import { describe, expect, it } from "vitest";

import type { PonyClip } from "./ponyMotion";
import {
  ponyTextureCandidates,
  ponyTextureLayout,
  ponyTextureManifest,
  nextPonyTextureFallback,
  resolvePonyTexture
} from "./ponyTextures";

const clips: PonyClip[] = [
  "idle",
  "walk",
  "working",
  "phone",
  "coffee",
  "treadmill",
  "nap",
  "salute",
  "restroom"
];

describe("pony texture manifest", () => {
  it("defines a real animated and still texture for every clip", () => {
    expect(Object.keys(ponyTextureManifest).sort()).toEqual([...clips].sort());
    for (const clip of clips) {
      const asset = ponyTextureManifest[clip];
      expect(asset.animatedSrc).toMatch(/\.gif(?:\?|$)/);
      expect(asset.stillSrc).toMatch(/\.png(?:\?|$)/);
      expect(asset.sourceWidth).toBeGreaterThan(0);
      expect(asset.sourceHeight).toBeGreaterThan(0);
      expect(asset.scale).toBeGreaterThan(0);
    }
  });

  it("uses animated textures only when motion is enabled", () => {
    const animated = resolvePonyTexture("walk", true);
    const still = resolvePonyTexture("walk", false);

    expect(animated.mode).toBe("animated");
    expect(animated.src).toBe(animated.asset.animatedSrc);
    expect(still.mode).toBe("still");
    expect(still.src).toBe(still.asset.stillSrc);
    expect(ponyTextureCandidates(animated).map((texture) => texture.src)).toEqual([
      animated.asset.animatedSrc,
      animated.asset.stillSrc,
      resolvePonyTexture("idle", false).src
    ]);
  });

  it("reuses the real salute take for the phone pose", () => {
    expect(ponyTextureManifest.phone).toBe(ponyTextureManifest.salute);
    expect(resolvePonyTexture("phone", true).key).toBe(resolvePonyTexture("salute", true).key);
  });

  it("keeps every ground anchor on the stage origin after normalization", () => {
    for (const asset of Object.values(ponyTextureManifest)) {
      const layout = ponyTextureLayout(asset);
      const groundX = layout.leftPercent + (asset.groundX * asset.scale / 150) * 100;
      const groundY = layout.topPercent + (asset.groundY * asset.scale / 124) * 100;

      expect(groundX).toBeCloseTo(50, 6);
      expect(groundY).toBeCloseTo(100, 6);
      expect(layout.widthPercent).toBeGreaterThan(0);
      expect(layout.heightPercent).toBeGreaterThan(0);
    }
  });

  it("carries the fallback asset layout and facing with the fallback source", () => {
    const restroom = resolvePonyTexture("restroom", true);
    const still = nextPonyTextureFallback(restroom);
    const idle = still ? nextPonyTextureFallback(still) : null;

    expect(still?.asset).toBe(restroom.asset);
    expect(still?.mode).toBe("still");
    expect(idle?.asset).toBe(ponyTextureManifest.idle);
    expect(idle?.asset.nativeFacing).toBe("left");
    expect(idle ? ponyTextureLayout(idle.asset) : null).toEqual(
      ponyTextureLayout(ponyTextureManifest.idle)
    );
  });
});
