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

export function sectionForView(view: ViewKey): LocalLibrarySection {
  return localLibraryViewKeys.has(view) ? view as LocalLibrarySection : "gallery";
}
