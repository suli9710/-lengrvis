import type { ViewKey } from "../store";

export type LocalLibrarySection =
  | "apps"
  | "documents"
  | "documentOcr"
  | "papers"
  | "courseware"
  | "reports"
  | "gallery"
  | "imageOcr"
  | "people"
  | "places"
  | "timeline";

export const localLibraryViewKeys = new Set<ViewKey>([
  "apps",
  "documents",
  "documentOcr",
  "papers",
  "courseware",
  "reports",
  "gallery",
  "imageOcr",
  "people",
  "places",
  "timeline",
] as ViewKey[]);

export type LocalLibraryFamily = "knowledge" | "gallery";

export const knowledgeSectionKeys: LocalLibrarySection[] = [
  "apps",
  "documents",
  "documentOcr",
  "papers",
  "courseware",
  "reports",
];

export const gallerySectionKeys: LocalLibrarySection[] = [
  "gallery",
  "imageOcr",
  "people",
  "places",
  "timeline",
];

export function libraryFamilyForView(view: ViewKey): LocalLibraryFamily | null {
  if ((knowledgeSectionKeys as ViewKey[]).includes(view)) return "knowledge";
  if ((gallerySectionKeys as ViewKey[]).includes(view)) return "gallery";
  return null;
}

export function sectionForView(view: ViewKey): LocalLibrarySection {
  return localLibraryViewKeys.has(view) ? view as LocalLibrarySection : "gallery";
}
