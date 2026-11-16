import { cubicBezier } from "motion/react";
// The Home glide's timing (owner 2026-11-15), shared by Home's scene and the banners above it so both move together:
// 270 ms on an ease-out with a gentle start, so no node moves more than about 5 px in one frame.
export const GLIDE_S = 0.27;
export const GLIDE_EASE = [0.4, 0.55, 0.6, 1] as const;
// the same curve as a function: animations given a function run on the JavaScript clock, never on the compositor
// (an opacity animated there starts a frame before everything else), so the banner and the scene start together
export const glideEase = cubicBezier(...GLIDE_EASE);
