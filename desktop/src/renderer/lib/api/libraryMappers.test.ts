import { describe, expect, it } from "vitest";

import {
  fileClusterRequestFor,
  mapFileSearchResponse,
  mapIndexStatus,
  mapLocalLibraryResponse
} from "./libraryMappers";

describe("library mapper contracts", () => {
  it("normalizes index status counters and failure path labels", () => {
    expect(
      mapIndexStatus({
        status: "degraded",
        files_indexed: "12",
        chunks_indexed: 24,
        embeddings_indexed: "20",
        bytes_indexed: "4096",
        last_indexed_at: "2026-07-03T04:00:00Z",
        last_modified_at: "2026-07-03T03:55:00Z",
        retry_hint: "pick a smaller scope",
        latest_failure: {
          at: "2026-07-03T04:01:00Z",
          path: "C:\\Users\\Suli\\Huge",
          message: "timeout"
        }
      })
    ).toEqual({
      status: "degraded",
      filesIndexed: 12,
      chunksIndexed: 24,
      embeddingsIndexed: 20,
      bytesIndexed: 4096,
      lastIndexedAt: "2026-07-03T04:00:00Z",
      lastModifiedAt: "2026-07-03T03:55:00Z",
      retryHint: "pick a smaller scope",
      latestFailure: {
        at: "2026-07-03T04:01:00Z",
        pathLabel: "C:\\Users\\Suli\\Huge",
        message: "timeout"
      }
    });
  });

  it("maps search index and name results into a single ranked response", () => {
    const response = mapFileSearchResponse({
      index_results: [
        {
          file_id: "doc-1",
          path: "C:\\Docs\\plan.md",
          snippet: "release plan"
        }
      ],
      name_results: [
        {
          path: "C:\\Docs\\invoice.pdf",
          name: "invoice.pdf"
        }
      ],
      name_search: {
        count: "2",
        scanned: "50",
        truncated: true,
        status: "ok"
      },
      index_status: {
        status: "ready",
        files_indexed: 10
      }
    });

    expect(response.results).toEqual([
      {
        id: "doc-1",
        path: "C:\\Docs\\plan.md",
        match: "release plan",
        line: 1,
        score: 0.9
      },
      {
        id: "C:\\Docs\\invoice.pdf",
        path: "C:\\Docs\\invoice.pdf",
        match: "invoice.pdf",
        line: 1,
        score: 0.75
      }
    ]);
    expect(response.meta).toMatchObject({
      count: 2,
      scanned: 50,
      truncated: true,
      status: "ok",
      indexStatus: {
        status: "ready",
        filesIndexed: 10
      }
    });
  });

  it("maps local library scope summaries and item aliases", () => {
    const response = mapLocalLibraryResponse({
      section: "documents",
      roots: ["C:\\Docs"],
      scope_summary: {
        root_count: "1",
        root_labels: ["Docs"],
        has_authorized_roots: true,
        display_label: "Docs",
        raw_paths_available_for_local_actions: false,
        shareable_summary_has_raw_paths: false
      },
      items: [
        {
          id: "item-1",
          path: "C:\\Docs\\plan.md",
          path_label: "Docs\\plan.md",
          name: "plan.md",
          parent: "C:\\Docs",
          parent_label: "Docs",
          kind: "document",
          extension: ".md",
          mime_type: "text/markdown",
          size: 128,
          created_at: 1,
          modified_at: 2,
          preview_url: "file://preview",
          group_label: "Markdown",
          icon_url: "file://icon"
        }
      ],
      count: 1,
      total: 3,
      scanned: 20,
      truncated: false,
      stats: {
        size: 128,
        by_extension: { ".md": 1 }
      }
    });

    expect(response.scopeSummary).toMatchObject({
      rootCount: 1,
      rootLabels: ["Docs"],
      hasAuthorizedRoots: true,
      displayLabel: "Docs",
      rawPathsAvailableForLocalActions: false,
      shareableSummaryHasRawPaths: false
    });
    expect(response.items[0]).toMatchObject({
      id: "item-1",
      path: "C:\\Docs\\plan.md",
      pathLabel: "Docs\\plan.md",
      parentLabel: "Docs",
      mimeType: "text/markdown",
      groupLabel: "Markdown"
    });
    expect(response.stats.byExtension).toEqual({ ".md": 1 });
  });

  it("converts public cluster options to backend wire fields", () => {
    expect(
      fileClusterRequestFor({
        k: 4,
        groupBy: "folder",
        cluster_by: "image",
        imagePaths: ["C:\\Images\\a.png"],
        paths: ["C:\\Docs\\a.md"],
        images: ["inline-image"],
        limit: 50,
        metadataWeight: 0.35
      })
    ).toEqual({
      k: 4,
      group_by: "folder",
      cluster_by: "image",
      paths: ["C:\\Docs\\a.md"],
      image_paths: ["C:\\Images\\a.png"],
      images: ["inline-image"],
      limit: 50,
      metadata_weight: 0.35
    });
  });
});
