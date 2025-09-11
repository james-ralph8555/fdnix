import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import deque
import graph_tool.all as gt

logger = logging.getLogger("fdnix.dependency-graph")


class DependencyGraph:
    """Build and analyze package dependency graphs using graph-tool."""
    
    def __init__(self) -> None:
        self.graph = gt.Graph(directed=True)  # Directed graph for dependencies
        self.package_mapping = {}  # Map store paths to vertex indices
        self.node_id_to_vertex = {}  # Map node IDs to vertex indices
        self.vertex_to_node_id = {}  # Map vertex indices to node IDs
        
        # Vertex properties for metadata
        self.package_name_prop = self.graph.new_vertex_property("string")
        self.version_prop = self.graph.new_vertex_property("string")
        self.attr_path_prop = self.graph.new_vertex_property("string")
        self.drv_path_prop = self.graph.new_vertex_property("string")

        # Lightweight adjacency caches (built after edges are added)
        # These dramatically speed up repeated traversals while keeping memory overhead reasonable.
        self._out_adj: Optional[List[List[int]]] = None
        self._in_adj: Optional[List[List[int]]] = None
        
    def build_from_raw_packages(self, raw_packages: List[Dict[str, Any]]) -> None:
        """Build dependency graph from raw JSONL package data."""
        logger.info("Building dependency graph from %d packages...", len(raw_packages))
        
        # First pass: create vertices and build package mapping
        for pkg_data in raw_packages:
            try:
                attr_path = ".".join(pkg_data.get("attrPath", []))
                name = pkg_data.get("name", "")
                package_name, version = self._parse_name_version(name)
                
                if not package_name or package_name == "unknown":
                    continue
                    
                node_id = f"{package_name}-{version}"
                
                # Add vertex with metadata
                vertex = self.graph.add_vertex()
                vertex_idx = int(vertex)
                
                # Store metadata in vertex properties
                self.package_name_prop[vertex] = package_name
                self.version_prop[vertex] = version
                self.attr_path_prop[vertex] = attr_path
                self.drv_path_prop[vertex] = pkg_data.get("drvPath", "")
                
                # Build mappings
                self.node_id_to_vertex[node_id] = vertex_idx
                self.vertex_to_node_id[vertex_idx] = node_id
                
                # Map store path to vertex index for dependency resolution
                drv_path = pkg_data.get("drvPath", "")
                if drv_path:
                    self.package_mapping[drv_path] = vertex_idx
                    
            except Exception as e:
                logger.warning("Error processing package for graph: %s", e)
                continue
        
        # Second pass: add edges for dependencies
        for pkg_data in raw_packages:
            try:
                name = pkg_data.get("name", "")
                package_name, version = self._parse_name_version(name)
                
                if not package_name or package_name == "unknown":
                    continue
                    
                node_id = f"{package_name}-{version}"
                source_vertex_idx = self.node_id_to_vertex.get(node_id)
                if source_vertex_idx is None:
                    continue
                    
                # Process input dependencies
                input_drvs = pkg_data.get("inputDrvs", {})
                for dep_drv_path in input_drvs.keys():
                    target_vertex_idx = self.package_mapping.get(dep_drv_path)
                    if target_vertex_idx is not None and target_vertex_idx != source_vertex_idx:
                        self.graph.add_edge(source_vertex_idx, target_vertex_idx)
                        
            except Exception as e:
                logger.warning("Error adding edges for package: %s", e)
                continue
        
        # Build adjacency caches for fast, low-overhead traversals
        self._build_adjacency()

        logger.info("Built dependency graph with %d nodes and %d edges", 
                   self.graph.num_vertices(), self.graph.num_edges())
    
    def get_dependencies(self, node_id: str) -> List[str]:
        """Get direct dependencies of a package (what it depends on)."""
        vertex_idx = self.node_id_to_vertex.get(node_id)
        if vertex_idx is None:
            return []

        if self._out_adj is None:
            self._build_adjacency()

        deps: List[str] = []
        for neighbor_idx in self._out_adj[vertex_idx]:
            neighbor_node_id = self.vertex_to_node_id.get(neighbor_idx)
            if neighbor_node_id:
                deps.append(neighbor_node_id)
        return deps
    
    def get_dependents(self, node_id: str) -> List[str]:
        """Get direct dependents of a package (what depends on it)."""
        vertex_idx = self.node_id_to_vertex.get(node_id)
        if vertex_idx is None:
            return []

        if self._in_adj is None:
            self._build_adjacency()

        deps: List[str] = []
        for neighbor_idx in self._in_adj[vertex_idx]:
            neighbor_node_id = self.vertex_to_node_id.get(neighbor_idx)
            if neighbor_node_id:
                deps.append(neighbor_node_id)
        return deps
    
    def get_all_dependencies(self, node_id: str) -> List[str]:
        """Get all transitive dependencies of a package."""
        vertex_idx = self.node_id_to_vertex.get(node_id)
        if vertex_idx is None:
            return []
        
        try:
            return self._get_descendants(vertex_idx)
        except Exception as e:
            logger.warning("Error calculating descendants for %s: %s", node_id, e)
            return []
    
    def get_all_dependents(self, node_id: str) -> List[str]:
        """Get all transitive dependents of a package."""
        vertex_idx = self.node_id_to_vertex.get(node_id)
        if vertex_idx is None:
            return []
        
        try:
            return self._get_ancestors(vertex_idx)
        except Exception as e:
            logger.warning("Error calculating ancestors for %s: %s", node_id, e)
            return []
    
    def _get_descendants(self, vertex_idx: int) -> List[str]:
        """Get all descendants (transitive dependencies) using BFS over cached adjacency."""
        if self._out_adj is None:
            self._build_adjacency()

        visited: Set[int] = set()
        queue: deque[int] = deque()
        descendants: List[str] = []

        # Seed with direct neighbors
        for neighbor_idx in self._out_adj[vertex_idx]:
            if neighbor_idx not in visited:
                visited.add(neighbor_idx)
                queue.append(neighbor_idx)

        while queue:
            current_idx = queue.popleft()
            node_id = self.vertex_to_node_id.get(current_idx)
            if node_id:
                descendants.append(node_id)
            # Add unvisited neighbors
            for nbr in self._out_adj[current_idx]:
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)

        return descendants
    
    def _get_ancestors(self, vertex_idx: int) -> List[str]:
        """Get all ancestors (transitive dependents) using BFS over cached adjacency."""
        if self._in_adj is None:
            self._build_adjacency()

        visited: Set[int] = set()
        queue: deque[int] = deque()
        ancestors: List[str] = []

        # Seed with direct predecessors
        for neighbor_idx in self._in_adj[vertex_idx]:
            if neighbor_idx not in visited:
                visited.add(neighbor_idx)
                queue.append(neighbor_idx)

        while queue:
            current_idx = queue.popleft()
            node_id = self.vertex_to_node_id.get(current_idx)
            if node_id:
                ancestors.append(node_id)
            # Add unvisited predecessors
            for nbr in self._in_adj[current_idx]:
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)

        return ancestors
    
    def get_dependency_info(self, node_id: str) -> Dict[str, Any]:
        """Get comprehensive dependency information for a package."""
        vertex_idx = self.node_id_to_vertex.get(node_id)
        if vertex_idx is None:
            return {
                "direct_dependencies": [],
                "direct_dependents": [],
                "all_dependencies": [],
                "all_dependents": [],
                "dependency_count": 0,
                "dependent_count": 0,
                "total_dependency_count": 0,
                "total_dependent_count": 0
            }
        
        direct_deps = self.get_dependencies(node_id)
        direct_dependents = self.get_dependents(node_id)
        all_deps = self.get_all_dependencies(node_id)
        all_dependents = self.get_all_dependents(node_id)
        
        return {
            "direct_dependencies": direct_deps,
            "direct_dependents": direct_dependents,
            "all_dependencies": all_deps,
            "all_dependents": all_dependents,
            "dependency_count": len(direct_deps),
            "dependent_count": len(direct_dependents),
            "total_dependency_count": len(all_deps),
            "total_dependent_count": len(all_dependents)
        }
    
    def get_node_metadata(self, node_id: str) -> Dict[str, Any]:
        """Get metadata for a graph node."""
        vertex_idx = self.node_id_to_vertex.get(node_id)
        if vertex_idx is None:
            return {}
        
        vertex = self.graph.vertex(vertex_idx)
        return {
            "package_name": self.package_name_prop[vertex],
            "version": self.version_prop[vertex],
            "attr_path": self.attr_path_prop[vertex],
            "drv_path": self.drv_path_prop[vertex]
        }
    
    def get_shortest_path(self, source: str, target: str) -> List[str]:
        """Get shortest dependency path between two packages."""
        source_idx = self.node_id_to_vertex.get(source)
        target_idx = self.node_id_to_vertex.get(target)
        
        if source_idx is None or target_idx is None:
            return []
        
        try:
            # Use BFS to find shortest path
            return self._bfs_shortest_path(source_idx, target_idx)
        except Exception as e:
            logger.warning("Error calculating path from %s to %s: %s", source, target, e)
            return []
    
    def _bfs_shortest_path(self, source_idx: int, target_idx: int) -> List[str]:
        """Find shortest path using BFS over cached adjacency."""
        if source_idx == target_idx:
            source_node_id = self.vertex_to_node_id.get(source_idx)
            return [source_node_id] if source_node_id else []

        if self._out_adj is None:
            self._build_adjacency()

        visited = {source_idx}
        queue: deque[Tuple[int, List[int]]] = deque([(source_idx, [source_idx])])

        while queue:
            current_idx, path = queue.popleft()
            for neighbor_idx in self._out_adj[current_idx]:
                if neighbor_idx == target_idx:
                    final_path = path + [neighbor_idx]
                    return [self.vertex_to_node_id[idx] for idx in final_path if idx in self.vertex_to_node_id]
                if neighbor_idx not in visited:
                    visited.add(neighbor_idx)
                    queue.append((neighbor_idx, path + [neighbor_idx]))
        
        return []  # No path found
    
    def find_circular_dependencies(self) -> List[List[str]]:
        """Find circular dependency cycles in the graph."""
        try:
            cycles = []
            visited = set()
            rec_stack = set()
            
            # DFS to find cycles
            for vertex in self.graph.vertices():
                vertex_idx = int(vertex)
                if vertex_idx not in visited:
                    cycle = self._dfs_find_cycles(vertex_idx, visited, rec_stack, [])
                    if cycle:
                        cycles.extend(cycle)
            
            if cycles:
                logger.warning("Found %d circular dependency cycles", len(cycles))
            return cycles
        except Exception as e:
            logger.error("Error finding circular dependencies: %s", e)
            return []
    
    def _dfs_find_cycles(self, vertex_idx: int, visited: set, rec_stack: set, path: List[int]) -> List[List[str]]:
        """DFS helper to find cycles."""
        visited.add(vertex_idx)
        rec_stack.add(vertex_idx)
        path.append(vertex_idx)
        cycles = []
        
        vertex = self.graph.vertex(vertex_idx)
        for neighbor in vertex.out_neighbors():
            neighbor_idx = int(neighbor)
            
            if neighbor_idx in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor_idx)
                cycle_vertices = path[cycle_start:] + [neighbor_idx]
                cycle_node_ids = [self.vertex_to_node_id.get(idx) for idx in cycle_vertices]
                cycle_node_ids = [nid for nid in cycle_node_ids if nid is not None]
                if cycle_node_ids:
                    cycles.append(cycle_node_ids)
            elif neighbor_idx not in visited:
                cycles.extend(self._dfs_find_cycles(neighbor_idx, visited, rec_stack, path))
        
        path.pop()
        rec_stack.remove(vertex_idx)
        return cycles
    
    def get_graph_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the dependency graph."""
        num_nodes = self.graph.num_vertices()
        num_edges = self.graph.num_edges()
        
        # Calculate connectivity stats
        try:
            # Use graph-tool's built-in functions for strongly/weakly connected components
            _, scc_hist = gt.label_components(self.graph, directed=True)
            strongly_connected = len(scc_hist)

            _, wcc_hist = gt.label_components(self.graph, directed=False)
            weakly_connected = len(wcc_hist)
        except Exception as e:
            logger.warning("Error calculating connectivity: %s", e)
            strongly_connected = 0
            weakly_connected = 0
        
        # Calculate degree statistics without materializing full degree lists
        max_in = 0
        max_out = 0
        zero_in = 0
        zero_out = 0
        
        if self._out_adj is None or self._in_adj is None:
            # Fallback to graph-tool iteration if adjacency not yet built
            for v in self.graph.vertices():
                in_deg = v.in_degree()
                out_deg = v.out_degree()
                if in_deg == 0:
                    zero_in += 1
                if out_deg == 0:
                    zero_out += 1
                if in_deg > max_in:
                    max_in = in_deg
                if out_deg > max_out:
                    max_out = out_deg
        else:
            # Use cached adjacency for faster degree inspection
            for idx in range(num_nodes):
                out_deg = len(self._out_adj[idx])
                in_deg = len(self._in_adj[idx])
                if in_deg == 0:
                    zero_in += 1
                if out_deg == 0:
                    zero_out += 1
                if in_deg > max_in:
                    max_in = in_deg
                if out_deg > max_out:
                    max_out = out_deg

        return {
            "total_packages": num_nodes,
            "total_dependencies": num_edges,
            "strongly_connected_components": strongly_connected,
            "weakly_connected_components": weakly_connected,
            "average_dependencies_per_package": (num_edges / num_nodes) if num_nodes > 0 else 0,
            "average_dependents_per_package": (num_edges / num_nodes) if num_nodes > 0 else 0,
            "max_dependencies": max_out,
            "max_dependents": max_in,
            "packages_with_no_dependencies": zero_out,
            "packages_with_no_dependents": zero_in,
        }
    
    def export_graph(self, output_path: str, format: str = "gexf") -> None:
        """Export the dependency graph to various formats for external analysis."""
        try:
            if format.lower() == "graphml":
                # Use graph-tool's native GraphML export
                self.graph.save(output_path, fmt="graphml")
            elif format.lower() == "gt":
                # Native graph-tool format (most efficient)
                self.graph.save(output_path)
            elif format.lower() == "edgelist":
                # Custom edgelist export
                self._export_edgelist(output_path)
            else:
                # Default to GraphML for compatibility
                logger.warning("Format %s not directly supported, using GraphML", format)
                self.graph.save(output_path, fmt="graphml")
            
            logger.info("Exported dependency graph to %s (format: %s)", output_path, format)
        except Exception as e:
            logger.error("Error exporting graph: %s", e)
    
    def _export_edgelist(self, output_path: str) -> None:
        """Export graph as edge list with node ID mappings."""
        with open(output_path, 'w') as f:
            # Write header
            f.write("# source target\n")
            
            # Write edges
            for edge in self.graph.edges():
                source_idx = int(edge.source())
                target_idx = int(edge.target())
                
                source_id = self.vertex_to_node_id.get(source_idx, f"vertex_{source_idx}")
                target_id = self.vertex_to_node_id.get(target_idx, f"vertex_{target_idx}")
                
                f.write(f"{source_id} {target_id}\n")
    
    def _parse_name_version(self, name: str) -> Tuple[str, str]:
        """Parse package name and version from nix-eval-jobs name field."""
        if not name:
            return "unknown", "unknown"
        
        # Nix package names are typically in format "name-version"
        parts = name.split('-')
        if len(parts) < 2:
            return name, "unknown"
        
        # Try to find where version starts (usually a digit or 'v')
        for i, part in enumerate(parts):
            if part and (part[0].isdigit() or part.startswith('v')):
                package_name = '-'.join(parts[:i])
                version = '-'.join(parts[i:])
                return package_name if package_name else name, version
        
        # Fallback: treat last part as version
        return '-'.join(parts[:-1]), parts[-1]

    def _build_adjacency(self) -> None:
        """Build cached adjacency lists for faster traversals and stats."""
        num_nodes = int(self.graph.num_vertices())
        out_adj: List[List[int]] = [[] for _ in range(num_nodes)]
        in_adj: List[List[int]] = [[] for _ in range(num_nodes)]

        # Use index-based iteration to avoid descriptor overhead
        for s, t in self.graph.iter_edges():
            out_adj[s].append(t)
            in_adj[t].append(s)

        self._out_adj = out_adj
        self._in_adj = in_adj


class DependencyGraphProcessor:
    """High-level processor for creating and managing dependency graphs."""
    
    def __init__(self) -> None:
        self.graph = DependencyGraph()
        
    def process_packages(self, raw_packages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process raw packages and return comprehensive dependency information."""
        logger.info("Processing dependency graph from %d packages...", len(raw_packages))
        
        # Build the graph
        self.graph.build_from_raw_packages(raw_packages)
        
        # Calculate dependency information for all packages
        dependency_data = {}
        node_count = 0
        
        for node_id in self.graph.vertex_to_node_id.values():
            try:
                # Get comprehensive dependency info
                dep_info = self.graph.get_dependency_info(node_id)
                node_metadata = self.graph.get_node_metadata(node_id)
                
                # Combine metadata and dependency info
                dependency_data[node_id] = {
                    **node_metadata,
                    **dep_info
                }
                
                node_count += 1
                if node_count % 1000 == 0:
                    logger.info("Processed dependency info for %d nodes...", node_count)
                    
            except Exception as e:
                logger.warning("Error processing dependency info for %s: %s", node_id, e)
                continue
        
        # Get graph statistics
        graph_stats = self.graph.get_graph_stats()
        
        # Find circular dependencies
        circular_deps = self.graph.find_circular_dependencies()
        if circular_deps:
            logger.warning("Found %d circular dependency cycles", len(circular_deps))
        
        logger.info("Dependency graph processing completed successfully")
        
        return {
            "dependency_data": dependency_data,
            "graph_stats": graph_stats,
            "circular_dependencies": circular_deps[:50]  # Limit to first 50 cycles
        }
    
    def export_graph(self, output_path: str, format: str = "gexf") -> None:
        """Export the dependency graph for external analysis."""
        self.graph.export_graph(output_path, format)
