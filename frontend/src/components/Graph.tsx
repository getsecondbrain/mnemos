import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import ForceGraph2D from "react-force-graph-2d";
import { listMemories, getAllConnections } from "../services/api";
import { useEncryption } from "../hooks/useEncryption";
import { hexToBuffer } from "../services/crypto";
import type { Memory } from "../types";

interface GraphNode {
  id: string;
  title: string;
  contentType: string;
  capturedAt: string;
}

interface GraphLink {
  source: string;
  target: string;
  relationshipType: string;
  strength: number;
  isPrimary: boolean;
  id: string;
  explanationEncrypted: string;
  explanationDek: string;
  encryptionAlgo: string;
  encryptionVersion: number;
}

const NODE_COLORS: Record<string, string> = {
  text: "#3b82f6",
  photo: "#10b981",
  voice: "#f59e0b",
  video: "#ef4444",
  document: "#8b5cf6",
  email: "#06b6d4",
  webpage: "#ec4899",
  default: "#6b7280",
};

const LINK_COLORS: Record<string, string> = {
  related: "#6b7280",
  caused_by: "#f59e0b",
  contradicts: "#ef4444",
  supports: "#10b981",
  references: "#3b82f6",
  extends: "#8b5cf6",
  summarizes: "#06b6d4",
  default: "#4b5563",
};

export default function Graph() {
  const [graphData, setGraphData] = useState<{
    nodes: GraphNode[];
    links: GraphLink[];
  }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const navigate = useNavigate();
  const { decrypt } = useEncryption();
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null);
  const explanationCache = useRef<Map<string, string>>(new Map());
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    text: string;
    relationshipType: string;
    strength: number;
  } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const decryptTitle = useCallback(
    async (memory: Memory): Promise<string> => {
      if (!memory.title_dek) return memory.title;
      try {
        const envelope = {
          ciphertext: hexToBuffer(memory.title),
          encryptedDek: hexToBuffer(memory.title_dek),
          algo: memory.encryption_algo ?? "aes-256-gcm",
          version: memory.encryption_version ?? 1,
        };
        const plaintext = await decrypt(envelope);
        return new TextDecoder().decode(plaintext);
      } catch {
        return "[Decryption failed]";
      }
    },
    [decrypt],
  );

  const decryptExplanation = useCallback(
    async (link: GraphLink): Promise<string> => {
      if (explanationCache.current.has(link.id)) {
        return explanationCache.current.get(link.id)!;
      }
      try {
        const envelope = {
          ciphertext: hexToBuffer(link.explanationEncrypted),
          encryptedDek: hexToBuffer(link.explanationDek),
          algo: link.encryptionAlgo,
          version: link.encryptionVersion,
        };
        const plaintext = await decrypt(envelope);
        const text = new TextDecoder().decode(plaintext);
        explanationCache.current.set(link.id, text);
        return text;
      } catch {
        return "[Decryption failed]";
      }
    },
    [decrypt],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadGraph() {
      setLoading(true);
      setError(null);
      try {
        const [memories, connections] = await Promise.all([
          listMemories({ limit: 200 }),
          getAllConnections(),
        ]);

        if (cancelled) return;

        const nodes: GraphNode[] = await Promise.all(
          memories.map(async (m) => ({
            id: m.id,
            title: await decryptTitle(m),
            contentType: m.content_type,
            capturedAt: m.captured_at,
          })),
        );

        const nodeIds = new Set(nodes.map((n) => n.id));

        const links: GraphLink[] = connections
          .filter(
            (c) =>
              nodeIds.has(c.source_memory_id) &&
              nodeIds.has(c.target_memory_id),
          )
          .map((c) => ({
            source: c.source_memory_id,
            target: c.target_memory_id,
            relationshipType: c.relationship_type,
            strength: c.strength,
            isPrimary: c.is_primary,
            id: c.id,
            explanationEncrypted: c.explanation_encrypted,
            explanationDek: c.explanation_dek,
            encryptionAlgo: c.encryption_algo,
            encryptionVersion: c.encryption_version,
          }));

        if (!cancelled) {
          setGraphData({ nodes, links });
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load graph",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadGraph();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-gray-400">Loading memory graph...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-red-400">{error}</p>
      </div>
    );
  }

  if (graphData.nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center">
        <p className="text-gray-400 text-lg mb-2">No memories yet</p>
        <p className="text-gray-500 text-sm">
          Add memories to see the neural network grow.
        </p>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="h-full w-full relative">
      <ForceGraph2D
        ref={graphRef}
        graphData={graphData}
        nodeId="id"
        nodeLabel="title"
        nodeColor={(node: { contentType?: string }) =>
          (NODE_COLORS[node.contentType ?? ""] ?? NODE_COLORS.default) as string
        }
        nodeRelSize={6}
        nodeCanvasObjectMode={() => "replace" as const}
        nodeCanvasObject={(
          node: { x?: number; y?: number; contentType?: string; title?: string },
          ctx: CanvasRenderingContext2D,
          globalScale: number,
        ) => {
          const size = 6;
          ctx.beginPath();
          ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI);
          ctx.fillStyle =
            NODE_COLORS[node.contentType ?? ""] ?? NODE_COLORS.default ?? "#6b7280";
          ctx.fill();
          if (globalScale > 1.5 && node.title) {
            ctx.font = `${12 / globalScale}px sans-serif`;
            ctx.fillStyle = "#e5e7eb";
            ctx.textAlign = "center";
            ctx.fillText(
              node.title.slice(0, 20),
              node.x!,
              node.y! + size + 10 / globalScale,
            );
          }
        }}
        linkColor={(link: { relationshipType?: string }) =>
          (LINK_COLORS[link.relationshipType ?? ""] ?? LINK_COLORS.default) as string
        }
        linkWidth={(link: { strength?: number }) =>
          Math.max(1, (link.strength ?? 0.5) * 3)
        }
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkLabel={(link: GraphLink) =>
          `${link.relationshipType} (${(link.strength * 100).toFixed(0)}%) — click for details`
        }
        onNodeClick={(node: { id?: string | number }) =>
          navigate(`/memory/${node.id}`)
        }
        onLinkClick={async (link: GraphLink, event: MouseEvent) => {
          const rect = containerRef.current?.getBoundingClientRect();
          const offsetX = rect?.left ?? 0;
          const offsetY = rect?.top ?? 0;
          const text = await decryptExplanation(link);
          setTooltip({
            x: event.clientX - offsetX,
            y: event.clientY - offsetY,
            text,
            relationshipType: link.relationshipType,
            strength: link.strength,
          });
        }}
        onBackgroundClick={() => setTooltip(null)}
        backgroundColor="#030712"
        width={dimensions.width}
        height={dimensions.height}
      />

      {/* Connection explanation tooltip */}
      {tooltip && (
        <div
          className="absolute z-50 max-w-sm bg-gray-800 border border-gray-700 rounded-lg p-4 shadow-xl"
          style={{
            left: Math.min(tooltip.x, dimensions.width - 320),
            top: Math.min(tooltip.y, dimensions.height - 200),
          }}
        >
          <div className="flex items-center justify-between mb-2">
            <span
              className="text-xs font-semibold px-2 py-0.5 rounded"
              style={{
                backgroundColor: LINK_COLORS[tooltip.relationshipType] ?? LINK_COLORS.default,
                color: "white",
              }}
            >
              {tooltip.relationshipType}
            </span>
            <span className="text-xs text-gray-500">
              Strength: {(tooltip.strength * 100).toFixed(0)}%
            </span>
            <button
              onClick={() => setTooltip(null)}
              className="text-gray-500 hover:text-gray-300 ml-2"
            >
              ×
            </button>
          </div>
          <p className="text-sm text-gray-300">{tooltip.text}</p>
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-4 left-4 bg-gray-900/80 backdrop-blur-sm rounded-lg p-3 text-xs">
        <p className="text-gray-400 font-semibold mb-2">Nodes</p>
        <div className="space-y-1 mb-3">
          {Object.entries(NODE_COLORS)
            .filter(([k]) => k !== "default")
            .map(([type, color]) => (
              <div key={type} className="flex items-center gap-2">
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: color }}
                />
                <span className="text-gray-400">{type}</span>
              </div>
            ))}
        </div>
        <p className="text-gray-400 font-semibold mb-2">Links</p>
        <div className="space-y-1">
          {Object.entries(LINK_COLORS)
            .filter(([k]) => k !== "default")
            .map(([type, color]) => (
              <div key={type} className="flex items-center gap-2">
                <div
                  className="w-4 h-0.5"
                  style={{ backgroundColor: color }}
                />
                <span className="text-gray-400">{type}</span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
