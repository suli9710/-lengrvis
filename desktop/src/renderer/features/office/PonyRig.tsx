import {
  type CSSProperties,
  type MutableRefObject,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  officeTurnDurationMs,
  ponyClipEnterDurationMs,
  ponyClipSwitchDurationMs,
  type OfficeFacing,
  type OfficeTravelPhase,
  type PonyClip
} from "./ponyMotion";
import {
  nextPonyTextureFallback,
  ponyTextureCandidates,
  ponyTextureLayout,
  resolvePonyTexture,
  type ResolvedPonyTexture
} from "./ponyTextures";

export type PonyFeedback = "selected" | "completed" | "failed" | "approval";

type RigStyle = CSSProperties & Record<`--${string}`, string | number>;

interface PonyRigProps {
  accent: string;
  clip: PonyClip;
  facing: OfficeFacing;
  motionPhase: OfficeTravelPhase;
  feedback?: PonyFeedback;
  isLead?: boolean;
  animate: boolean;
  paused?: boolean;
}

const loadedTextureSources = new Set<string>();
const textureLoadRequests = new Map<string, Promise<boolean>>();

export function PonyRig({
  accent,
  clip,
  facing,
  motionPhase,
  feedback,
  isLead,
  animate,
  paused = false
}: PonyRigProps) {
  const requestedTexture = useMemo(
    () => resolvePonyTexture(clip, animate && !paused),
    [animate, clip, paused]
  );
  const [activeTexture, setActiveTexture] = useState(requestedTexture);
  const [outgoingTexture, setOutgoingTexture] = useState<ResolvedPonyTexture | null>(null);
  const activeTextureRef = useRef(activeTexture);
  const activeSlotRef = useRef<HTMLSpanElement>(null);
  const outgoingSlotRef = useRef<HTMLSpanElement>(null);
  const feedbackRef = useRef<HTMLSpanElement>(null);
  const facingRef = useRef<HTMLSpanElement>(null);
  const transitionAnimationsRef = useRef<Animation[]>([]);
  const facingAnimationRef = useRef<Animation | null>(null);
  const feedbackAnimationRef = useRef<Animation | null>(null);
  const previousFacingRef = useRef(facing);
  const loadIdRef = useRef(0);
  const transitionIdRef = useRef(0);
  const animateEffects = animate && !paused;

  useLayoutEffect(() => {
    const current = activeTextureRef.current;
    if (current.key === requestedTexture.key) {
      if (current.clip !== requestedTexture.clip) {
        activeTextureRef.current = requestedTexture;
        setActiveTexture(requestedTexture);
      }
      return;
    }

    const loadId = ++loadIdRef.current;
    if (!animateEffects) {
      activeTextureRef.current = requestedTexture;
      transitionIdRef.current += 1;
      cancelAnimations(transitionAnimationsRef);
      setOutgoingTexture(null);
      setActiveTexture(requestedTexture);
      return;
    }

    let cancelled = false;
    preloadPonyTexture(requestedTexture).then((nextTexture) => {
      if (cancelled || loadId !== loadIdRef.current || !nextTexture) return;
      const previous = activeTextureRef.current;
      activeTextureRef.current = nextTexture;
      transitionIdRef.current += 1;
      cancelAnimations(transitionAnimationsRef);
      setOutgoingTexture(animateEffects ? previous : null);
      setActiveTexture(nextTexture);
    }, () => undefined);

    return () => {
      cancelled = true;
    };
  }, [animateEffects, requestedTexture]);

  useLayoutEffect(() => {
    if (!outgoingTexture) return;
    const incoming = activeSlotRef.current;
    const outgoing = outgoingSlotRef.current;
    const transitionId = transitionIdRef.current;
    cancelAnimations(transitionAnimationsRef);

    if (!animateEffects || !incoming || !outgoing) {
      setOutgoingTexture(null);
      return;
    }

    const duration = activeTexture.clip === "walk" ? ponyClipEnterDurationMs : ponyClipSwitchDurationMs;
    const animations = [
      incoming.animate([{ opacity: 0 }, { opacity: 1 }], {
        duration,
        easing: "cubic-bezier(0.16, 1, 0.3, 1)",
        fill: "both"
      }),
      outgoing.animate([{ opacity: 1 }, { opacity: 0 }], {
        duration,
        easing: "ease-out",
        fill: "both"
      })
    ];
    transitionAnimationsRef.current = animations;
    Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
      if (transitionId !== transitionIdRef.current) return;
      incoming.style.opacity = "1";
      outgoing.style.opacity = "0";
      cancelAnimations(transitionAnimationsRef);
      setOutgoingTexture(null);
    });
  }, [activeTexture.clip, activeTexture.key, animateEffects, outgoingTexture]);

  useLayoutEffect(() => {
    const previousFacing = previousFacingRef.current;
    previousFacingRef.current = facing;
    const element = facingRef.current;
    const previousScale = previousFacing === "left" ? 1 : -1;
    const currentScale = element && facingAnimationRef.current
      ? readScaleX(getComputedStyle(element).transform, previousScale)
      : previousScale;
    facingAnimationRef.current?.cancel();
    facingAnimationRef.current = null;
    if (!element || previousFacing === facing || !animateEffects) return;

    const to = facing === "left" ? 1 : -1;
    const collapseDirection = Math.sign(currentScale || to);
    const animation = element.animate(
      [
        { transform: `scaleX(${currentScale}) scaleY(1)` },
        { transform: `scaleX(${collapseDirection * 0.12}) scaleY(1.045)`, offset: 0.5 },
        { transform: `scaleX(${to}) scaleY(1)` }
      ],
      { duration: officeTurnDurationMs, easing: "ease-in-out", fill: "both" }
    );
    facingAnimationRef.current = animation;
    animation.finished.then(
      () => {
        if (facingAnimationRef.current !== animation) return;
        animation.cancel();
        facingAnimationRef.current = null;
      },
      () => undefined
    );
  }, [animateEffects, facing]);

  useLayoutEffect(() => {
    const element = feedbackRef.current;
    feedbackAnimationRef.current?.cancel();
    feedbackAnimationRef.current = null;
    if (!element || !feedback || !animateEffects) return;

    const [frames, duration] = feedbackFrames(feedback);
    const animation = element.animate(frames, {
      duration,
      easing: feedback === "failed" ? "ease-in-out" : "cubic-bezier(0.22, 1.2, 0.36, 1)",
      fill: "none"
    });
    feedbackAnimationRef.current = animation;
    animation.finished.then(
      () => {
        if (feedbackAnimationRef.current === animation) feedbackAnimationRef.current = null;
      },
      () => undefined
    );
  }, [animateEffects, feedback]);

  useEffect(() => () => {
    loadIdRef.current += 1;
    transitionIdRef.current += 1;
    cancelAnimations(transitionAnimationsRef);
    facingAnimationRef.current?.cancel();
    feedbackAnimationRef.current?.cancel();
  }, []);

  const facingScale = facing === "left" ? 1 : -1;
  const style = { "--agent-accent": accent } as RigStyle;
  const handleActiveTextureError = (texture: ResolvedPonyTexture) => {
    const fallback = nextPonyTextureFallback(texture);
    if (!fallback) return;
    activeTextureRef.current = fallback;
    transitionIdRef.current += 1;
    cancelAnimations(transitionAnimationsRef);
    setOutgoingTexture(null);
    setActiveTexture(fallback);
  };
  const handleOutgoingTextureError = () => {
    transitionIdRef.current += 1;
    cancelAnimations(transitionAnimationsRef);
    if (activeSlotRef.current) activeSlotRef.current.style.opacity = "1";
    setOutgoingTexture(null);
  };

  return (
    <span
      className={`pony-agent-svg pony-rig ${isLead ? "pony-rig--lead" : "pony-rig--standard"}`}
      aria-hidden="true"
      style={style}
      data-facing={facing}
      data-motion-phase={motionPhase}
      data-clip={clip}
      data-texture-mode={activeTexture.mode}
    >
      <span ref={feedbackRef} className="pony-rig__feedback">
        <span ref={facingRef} className="pony-rig__facing" style={{ transform: `scaleX(${facingScale})` }}>
          {outgoingTexture ? (
            <TextureLayer
              key={`outgoing:${outgoingTexture.key}`}
              slotRef={outgoingSlotRef}
              className="pony-rig__slot pony-rig__slot--outgoing"
              texture={outgoingTexture}
              opacity={1}
              onError={handleOutgoingTextureError}
            />
          ) : null}
          <TextureLayer
            key={`active:${activeTexture.key}`}
            slotRef={activeSlotRef}
            className="pony-rig__slot pony-rig__slot--active"
            texture={activeTexture}
            opacity={outgoingTexture ? 0 : 1}
            onError={handleActiveTextureError}
          />
        </span>
      </span>
    </span>
  );
}

const TextureLayer = ({
  slotRef,
  className,
  texture,
  opacity,
  onError
}: {
  slotRef: MutableRefObject<HTMLSpanElement | null>;
  className: string;
  texture: ResolvedPonyTexture;
  opacity: number;
  onError: (texture: ResolvedPonyTexture) => void;
}) => {
  const layout = ponyTextureLayout(texture.asset);
  const nativeScale = texture.asset.nativeFacing === "left" ? 1 : -1;
  return (
    <span ref={slotRef} className={className} style={{ opacity }} data-texture-key={texture.key}>
      <span className="pony-rig__texture-frame" style={{ transform: `scaleX(${nativeScale})` }}>
        <img
          className="pony-rig__texture"
          src={texture.src}
          alt=""
          draggable={false}
          decoding="async"
          onError={() => onError(texture)}
          style={{
            left: `${layout.leftPercent}%`,
            top: `${layout.topPercent}%`,
            width: `${layout.widthPercent}%`,
            height: `${layout.heightPercent}%`
          }}
        />
      </span>
    </span>
  );
};

function preloadPonyTexture(texture: ResolvedPonyTexture) {
  const candidates = ponyTextureCandidates(texture);
  return loadFirstAvailableTexture(candidates);
}

async function loadFirstAvailableTexture(candidates: ResolvedPonyTexture[]) {
  if (typeof Image === "undefined") return candidates[0] ?? null;
  for (const candidate of candidates) {
    if (loadedTextureSources.has(candidate.src) || await requestTextureLoad(candidate.src)) return candidate;
  }
  return null;
}

function requestTextureLoad(src: string) {
  const inFlight = textureLoadRequests.get(src);
  if (inFlight) return inFlight;
  const request = canLoadTexture(src).then((loaded) => {
    textureLoadRequests.delete(src);
    if (loaded) loadedTextureSources.add(src);
    return loaded;
  });
  textureLoadRequests.set(src, request);
  return request;
}

function canLoadTexture(src: string) {
  return new Promise<boolean>((resolve) => {
    const image = new Image();
    let settled = false;
    const timeoutId = window.setTimeout(() => finish(false), 4000);
    const finish = (loaded: boolean) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      image.onload = null;
      image.onerror = null;
      resolve(loaded);
    };
    image.onload = () => {
      const decoded = image.decode?.();
      if (!decoded) {
        finish(true);
        return;
      }
      decoded.then(() => finish(true), () => finish(true));
    };
    image.onerror = () => finish(false);
    image.decoding = "async";
    image.src = src;
  });
}

function readScaleX(transform: string, fallback: number) {
  if (!transform || transform === "none") return fallback;
  const matrix3d = transform.match(/^matrix3d\((.+)\)$/);
  if (matrix3d) {
    const value = Number.parseFloat(matrix3d[1]?.split(",")[0] ?? "");
    return Number.isFinite(value) ? value : fallback;
  }
  const matrix = transform.match(/^matrix\((.+)\)$/);
  if (matrix) {
    const value = Number.parseFloat(matrix[1]?.split(",")[0] ?? "");
    return Number.isFinite(value) ? value : fallback;
  }
  return fallback;
}

function feedbackFrames(feedback: PonyFeedback): [Keyframe[], number] {
  if (feedback === "completed") {
    return [[
      { transform: "translateY(0) scale(1)" },
      { transform: "translateY(-8px) scale(1.035)", offset: 0.42 },
      { transform: "translateY(0) scale(1)" }
    ], 520];
  }
  if (feedback === "approval") {
    return [[
      { transform: "translateY(0) rotate(0)" },
      { transform: "translateY(-3px) rotate(-2deg)", offset: 0.45 },
      { transform: "translateY(0) rotate(0)" }
    ], 520];
  }
  if (feedback === "failed") {
    return [[
      { transform: "translateX(0) rotate(0)" },
      { transform: "translateX(-3px) rotate(-2deg)" },
      { transform: "translateX(3px) rotate(2deg)" },
      { transform: "translateX(0) rotate(0)" }
    ], 420];
  }
  return [[
    { transform: "translateY(0)" },
    { transform: "translateY(-6px)", offset: 0.45 },
    { transform: "translateY(0)" }
  ], 460];
}

function cancelAnimations(ref: MutableRefObject<Animation[]>) {
  for (const animation of ref.current) animation.cancel();
  ref.current = [];
}
