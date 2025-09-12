import type { PackageNodeData, GraphData, NodeData, LinkData } from '../types';

class GraphService {
  private cache = new Map<string, PackageNodeData>();
  private bucketUrl: string;

  constructor() {
    // Use CloudFront path for graph data
    this.bucketUrl = '/nodes';
  }

  async fetchPackageNode(nodeId: string): Promise<PackageNodeData | null> {
    if (this.cache.has(nodeId)) {
      return this.cache.get(nodeId)!;
    }

    try {
      const url = `${this.bucketUrl}/${nodeId}.json.br`;
      const response = await fetch(url, {
        headers: {
          'Accept': 'application/json',
          'Accept-Encoding': 'br, gzip, deflate',
        },
      });

      if (!response.ok) {
        if (response.status === 404) {
          console.warn(`Package node not found: ${nodeId}`);
          return null;
        }
        throw new Error(`Failed to fetch package node: ${response.statusText}`);
      }

      // Browser automatically handles brotli decompression when Content-Encoding: br is set
      const data: PackageNodeData = await response.json();

      this.cache.set(nodeId, data);
      return data;
    } catch (error) {
      console.error(`Error fetching package node ${nodeId}:`, error);
      return null;
    }
  }

  transformToGraphData(
    mainPackage: PackageNodeData,
    expandedNodes: Set<string> = new Set()
  ): GraphData {
    const nodes: NodeData[] = [];
    const links: LinkData[] = [];
    const processedNodes = new Set<string>();

    const addNode = (
      id: string,
      name: string,
      type: NodeData['type'],
      version?: string,
      category?: string,
      description?: string
    ) => {
      if (processedNodes.has(id)) return;
      
      nodes.push({
        id,
        name,
        type,
        version,
        category,
        description,
        expanded: expandedNodes.has(id),
      });
      processedNodes.add(id);
    };

    addNode(
      mainPackage.nodeId,
      mainPackage.packageName,
      'main',
      mainPackage.version,
      mainPackage.category,
      mainPackage.description
    );

    mainPackage.dependencies.direct.forEach((depName) => {
      // Use a placeholder version for dependency nodes since we don't have the actual version
      // These will be properly resolved when the node is expanded
      const depId = `${depName}-unknown`;
      addNode(depId, depName, 'dependency');
      
      links.push({
        source: mainPackage.nodeId,
        target: depId,
        type: 'dependency',
      });
    });

    mainPackage.dependents.direct.forEach((depName) => {
      // Use a placeholder for dependent nodes since we don't have the actual version
      // These will be properly resolved when the node is expanded
      const depId = `${depName}-unknown`;
      addNode(depId, depName, 'dependent');
      
      links.push({
        source: depId,
        target: mainPackage.nodeId,
        type: 'dependent',
      });
    });

    return { nodes, links };
  }

  async expandNode(nodeId: string, currentGraph: GraphData): Promise<GraphData> {
    const packageData = await this.fetchPackageNode(nodeId);
    if (!packageData) return currentGraph;

    const newNodes: NodeData[] = [...currentGraph.nodes];
    const newLinks: LinkData[] = [...currentGraph.links];
    const existingNodeIds = new Set(currentGraph.nodes.map(n => n.id));

    const targetNode = newNodes.find(n => n.id === nodeId);
    if (targetNode) {
      targetNode.expanded = true;
    }

    packageData.dependencies.direct.forEach((depName) => {
      // Use a placeholder version for dependency nodes since we don't know their actual versions
      const depId = `${depName}-unknown`;
      
      if (!existingNodeIds.has(depId)) {
        newNodes.push({
          id: depId,
          name: depName,
          type: 'dependency',
          expanded: false,
        });
      }

      const linkExists = newLinks.some(
        l => (l.source === nodeId || (typeof l.source === 'object' && l.source.id === nodeId)) &&
             (l.target === depId || (typeof l.target === 'object' && l.target.id === depId))
      );

      if (!linkExists) {
        newLinks.push({
          source: nodeId,
          target: depId,
          type: 'dependency',
        });
      }
    });

    packageData.dependents.direct.forEach((depName) => {
      // Use a placeholder for dependent nodes since we don't know their actual versions
      const depId = `${depName}-unknown`;
      
      if (!existingNodeIds.has(depId)) {
        newNodes.push({
          id: depId,
          name: depName,
          type: 'dependent',
          expanded: false,
        });
      }

      const linkExists = newLinks.some(
        l => (l.source === depId || (typeof l.source === 'object' && l.source.id === depId)) &&
             (l.target === nodeId || (typeof l.target === 'object' && l.target.id === nodeId))
      );

      if (!linkExists) {
        newLinks.push({
          source: depId,
          target: nodeId,
          type: 'dependent',
        });
      }
    });

    return { nodes: newNodes, links: newLinks };
  }

  clearCache(): void {
    this.cache.clear();
  }

  getCacheSize(): number {
    return this.cache.size;
  }
}

export const graphService = new GraphService();