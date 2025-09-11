import { createSignal, createEffect, onCleanup, onMount, Show } from 'solid-js';
import type { DependencyGraphProps, GraphData, NodeData, LinkData } from '../types';
import { graphService } from '../services/graphService';
import { LoadingSpinner } from './LoadingSpinner';

// Lazy load D3 modules
const loadD3 = async () => {
  const [
    { forceSimulation, forceManyBody, forceCenter, forceLink, forceCollide },
    { select },
    { drag },
    { zoom, zoomIdentity }
  ] = await Promise.all([
    import('d3-force'),
    import('d3-selection'),
    import('d3-drag'),
    import('d3-zoom')
  ]);

  return {
    forceSimulation,
    forceManyBody,
    forceCenter,
    forceLink,
    forceCollide,
    select,
    drag,
    zoom,
    zoomIdentity
  };
};

export function DependencyGraph(props: DependencyGraphProps) {
  const [loading, setLoading] = createSignal(true);
  const [error, setError] = createSignal<string | null>(null);
  const [graphData, setGraphData] = createSignal<GraphData>({ nodes: [], links: [] });
  const [selectedNode, setSelectedNode] = createSignal<NodeData | null>(null);
  
  let canvasRef: HTMLCanvasElement | undefined;
  let containerRef: HTMLDivElement | undefined;
  let simulation: any;
  let d3: any;
  let transform = { x: 0, y: 0, k: 1 };

  const nodeColors = {
    main: '#3B82F6',      // blue-500
    dependency: '#10B981', // emerald-500  
    dependent: '#F59E0B'   // amber-500
  };

  const initializeGraph = async () => {
    try {
      setLoading(true);
      setError(null);

      // Load D3 modules
      d3 = await loadD3();

      // Fetch initial package data
      const packageData = await graphService.fetchPackageNode(props.packageName);
      if (!packageData) {
        setError(`Package "${props.packageName}" not found`);
        return;
      }

      // Transform to graph data
      const initialGraph = graphService.transformToGraphData(packageData);
      setGraphData(initialGraph);

      // Initialize D3 simulation
      setupSimulation(initialGraph);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dependency graph');
    } finally {
      setLoading(false);
    }
  };

  const setupSimulation = (data: GraphData) => {
    if (!canvasRef || !d3) return;

    const width = canvasRef.width;
    const height = canvasRef.height;

    // Stop existing simulation
    if (simulation) {
      simulation.stop();
    }

    // Create new simulation
    simulation = d3.forceSimulation(data.nodes)
      .force('link', d3.forceLink(data.links).id((d: NodeData) => d.id).distance(100))
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(30))
      .on('tick', draw);

    // Setup zoom and drag
    setupInteractions();
  };

  const setupInteractions = () => {
    if (!canvasRef || !d3) return;

    const canvas = d3.select(canvasRef);
    
    // Zoom behavior
    const zoomBehavior = d3.zoom()
      .scaleExtent([0.1, 5])
      .on('zoom', (event: any) => {
        transform = event.transform;
        draw();
      });

    canvas.call(zoomBehavior);

    // Click/drag interactions
    canvas.on('click', (event: MouseEvent) => {
      const [x, y] = getMousePosition(event);
      const node = findNodeAtPosition(x, y);
      
      if (node) {
        handleNodeClick(node);
      } else {
        setSelectedNode(null);
      }
    });

    // Drag nodes
    const dragBehavior = d3.drag()
      .on('start', (event: any) => {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        const node = findNodeAtPosition(event.x, event.y);
        if (node) {
          node.fx = node.x;
          node.fy = node.y;
        }
      })
      .on('drag', (event: any) => {
        const node = findNodeAtPosition(event.x, event.y);
        if (node) {
          node.fx = event.x;
          node.fy = event.y;
        }
      })
      .on('end', (event: any) => {
        if (!event.active) simulation.alphaTarget(0);
        const node = findNodeAtPosition(event.x, event.y);
        if (node) {
          node.fx = null;
          node.fy = null;
        }
      });

    canvas.call(dragBehavior);
  };

  const getMousePosition = (event: MouseEvent): [number, number] => {
    if (!canvasRef) return [0, 0];
    
    const rect = canvasRef.getBoundingClientRect();
    const x = (event.clientX - rect.left - transform.x) / transform.k;
    const y = (event.clientY - rect.top - transform.y) / transform.k;
    return [x, y];
  };

  const findNodeAtPosition = (x: number, y: number): NodeData | null => {
    const data = graphData();
    const nodeRadius = 20;
    
    return data.nodes.find(node => {
      if (typeof node.x === 'undefined' || typeof node.y === 'undefined') return false;
      const dx = node.x - x;
      const dy = node.y - y;
      return Math.sqrt(dx * dx + dy * dy) < nodeRadius;
    }) || null;
  };

  const handleNodeClick = async (node: NodeData) => {
    setSelectedNode(node);
    
    if (node.type !== 'main' && !node.expanded) {
      try {
        setLoading(true);
        const expandedGraph = await graphService.expandNode(node.id, graphData());
        setGraphData(expandedGraph);
        setupSimulation(expandedGraph);
      } catch (err) {
        setError(`Failed to expand node: ${err instanceof Error ? err.message : 'Unknown error'}`);
      } finally {
        setLoading(false);
      }
    }
  };

  const draw = () => {
    if (!canvasRef) return;
    
    const context = canvasRef.getContext('2d');
    if (!context) return;

    const width = canvasRef.width;
    const height = canvasRef.height;
    const data = graphData();

    // Clear canvas
    context.clearRect(0, 0, width, height);
    
    // Apply transform
    context.save();
    context.translate(transform.x, transform.y);
    context.scale(transform.k, transform.k);

    // Draw links
    context.strokeStyle = '#E5E7EB'; // gray-200
    context.lineWidth = 2;
    context.globalAlpha = 0.6;
    
    data.links.forEach(link => {
      const source = typeof link.source === 'object' ? link.source : data.nodes.find(n => n.id === link.source);
      const target = typeof link.target === 'object' ? link.target : data.nodes.find(n => n.id === link.target);
      
      if (source && target && source.x !== undefined && source.y !== undefined && 
          target.x !== undefined && target.y !== undefined) {
        context.beginPath();
        context.moveTo(source.x, source.y);
        context.lineTo(target.x, target.y);
        context.stroke();
      }
    });

    // Draw nodes
    context.globalAlpha = 1;
    
    data.nodes.forEach(node => {
      if (node.x === undefined || node.y === undefined) return;
      
      const radius = node.type === 'main' ? 25 : 20;
      const color = nodeColors[node.type];
      
      // Node circle
      context.fillStyle = color;
      context.beginPath();
      context.arc(node.x, node.y, radius, 0, 2 * Math.PI);
      context.fill();
      
      // Selected node highlight
      if (selectedNode()?.id === node.id) {
        context.strokeStyle = '#1F2937'; // gray-800
        context.lineWidth = 3;
        context.stroke();
      }
      
      // Expansion indicator for unexpanded nodes
      if (node.type !== 'main' && !node.expanded) {
        context.fillStyle = '#FFFFFF';
        context.beginPath();
        context.arc(node.x + radius - 5, node.y - radius + 5, 5, 0, 2 * Math.PI);
        context.fill();
        context.fillStyle = '#374151'; // gray-700
        context.font = '8px sans-serif';
        context.textAlign = 'center';
        context.fillText('+', node.x + radius - 5, node.y - radius + 8);
      }
      
      // Node label
      context.fillStyle = '#FFFFFF';
      context.font = node.type === 'main' ? 'bold 12px sans-serif' : '10px sans-serif';
      context.textAlign = 'center';
      context.fillText(node.name, node.x, node.y + 4);
    });

    context.restore();
  };

  onMount(() => {
    if (canvasRef && containerRef) {
      // Set canvas size
      const rect = containerRef.getBoundingClientRect();
      canvasRef.width = rect.width;
      canvasRef.height = rect.height;
      
      // Initialize graph
      initializeGraph();
    }
  });

  onCleanup(() => {
    if (simulation) {
      simulation.stop();
    }
  });

  createEffect(() => {
    // Redraw when graph data changes
    if (graphData().nodes.length > 0) {
      draw();
    }
  });

  return (
    <div class="fixed inset-0 z-50 bg-black bg-opacity-50 flex items-center justify-center">
      <div class="bg-white rounded-lg shadow-xl max-w-6xl w-full mx-4 h-5/6 flex flex-col">
        {/* Header */}
        <div class="flex items-center justify-between p-4 border-b">
          <div>
            <h2 class="text-xl font-semibold text-gray-900">Dependency Graph</h2>
            <p class="text-sm text-gray-600">Package: {props.packageName}</p>
          </div>
          <button
            onClick={props.onClose}
            class="text-gray-400 hover:text-gray-600 text-2xl leading-none"
          >
            ×
          </button>
        </div>

        {/* Graph Container */}
        <div class="flex-1 relative" ref={containerRef}>
          <Show when={loading()}>
            <div class="absolute inset-0 flex items-center justify-center bg-white bg-opacity-75 z-10">
              <div class="text-center">
                <LoadingSpinner />
                <p class="mt-2 text-gray-600">Loading dependency graph...</p>
              </div>
            </div>
          </Show>

          <Show when={error()}>
            <div class="absolute inset-0 flex items-center justify-center">
              <div class="text-center text-red-600">
                <p class="text-lg font-medium">Error</p>
                <p class="mt-2">{error()}</p>
                <button
                  onClick={initializeGraph}
                  class="mt-4 px-4 py-2 bg-red-100 hover:bg-red-200 rounded text-red-800"
                >
                  Retry
                </button>
              </div>
            </div>
          </Show>

          <canvas
            ref={canvasRef}
            class="w-full h-full cursor-grab active:cursor-grabbing"
            style={{ display: loading() || error() ? 'none' : 'block' }}
          />
        </div>

        {/* Legend and Info */}
        <div class="p-4 border-t bg-gray-50">
          <div class="flex justify-between items-center text-sm">
            <div class="flex space-x-6">
              <div class="flex items-center">
                <div class="w-4 h-4 rounded-full bg-blue-500 mr-2"></div>
                <span>Main Package</span>
              </div>
              <div class="flex items-center">
                <div class="w-4 h-4 rounded-full bg-emerald-500 mr-2"></div>
                <span>Dependencies</span>
              </div>
              <div class="flex items-center">
                <div class="w-4 h-4 rounded-full bg-amber-500 mr-2"></div>
                <span>Dependents</span>
              </div>
            </div>
            <div class="text-gray-600">
              Click nodes to expand • Drag to move • Scroll to zoom
            </div>
          </div>

          <Show when={selectedNode()}>
            <div class="mt-3 p-3 bg-white rounded border">
              <div class="flex justify-between items-start">
                <div>
                  <h4 class="font-medium">{selectedNode()?.name}</h4>
                  <Show when={selectedNode()?.version}>
                    <p class="text-sm text-gray-600">Version: {selectedNode()?.version}</p>
                  </Show>
                  <Show when={selectedNode()?.description}>
                    <p class="text-sm text-gray-700 mt-1">{selectedNode()?.description}</p>
                  </Show>
                </div>
                <span class="text-xs px-2 py-1 rounded bg-gray-100 text-gray-700 capitalize">
                  {selectedNode()?.type}
                </span>
              </div>
            </div>
          </Show>
        </div>
      </div>
    </div>
  );
}