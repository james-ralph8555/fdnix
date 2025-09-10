import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import networkx as nx

logger = logging.getLogger("fdnix.dependency-graph")


class DependencyGraph:
    """Build and analyze package dependency graphs using NetworkX."""
    
    def __init__(self) -> None:
        self.graph = nx.DiGraph()  # Directed graph for dependencies
        self.package_mapping = {}  # Map store paths to package identifiers
        
    def build_from_raw_packages(self, raw_packages: List[Dict[str, Any]]) -> None:
        """Build dependency graph from raw JSONL package data."""
        logger.info("Building dependency graph from %d packages...", len(raw_packages))
        
        # First pass: create nodes and build package mapping
        for pkg_data in raw_packages:
            try:
                attr_path = ".".join(pkg_data.get("attrPath", []))
                name = pkg_data.get("name", "")
                package_name, version = self._parse_name_version(name)
                
                if not package_name or package_name == "unknown":
                    continue
                    
                node_id = f"{package_name}-{version}"
                
                # Add node with metadata
                self.graph.add_node(node_id, 
                                  package_name=package_name,
                                  version=version,
                                  attr_path=attr_path,
                                  drv_path=pkg_data.get("drvPath", ""))
                
                # Map store path to node ID for dependency resolution
                drv_path = pkg_data.get("drvPath", "")
                if drv_path:
                    self.package_mapping[drv_path] = node_id
                    
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
                    
                source_node = f"{package_name}-{version}"
                if source_node not in self.graph:
                    continue
                    
                # Process input dependencies
                input_drvs = pkg_data.get("inputDrvs", {})
                for dep_drv_path in input_drvs.keys():
                    target_node = self.package_mapping.get(dep_drv_path)
                    if target_node and target_node != source_node:
                        self.graph.add_edge(source_node, target_node)
                        
            except Exception as e:
                logger.warning("Error adding edges for package: %s", e)
                continue
        
        logger.info("Built dependency graph with %d nodes and %d edges", 
                   self.graph.number_of_nodes(), self.graph.number_of_edges())
    
    def get_dependencies(self, node_id: str) -> List[str]:
        """Get direct dependencies of a package (what it depends on)."""
        if node_id not in self.graph:
            return []
        return list(self.graph.successors(node_id))
    
    def get_dependents(self, node_id: str) -> List[str]:
        """Get direct dependents of a package (what depends on it)."""
        if node_id not in self.graph:
            return []
        return list(self.graph.predecessors(node_id))
    
    def get_all_dependencies(self, node_id: str) -> List[str]:
        """Get all transitive dependencies of a package."""
        if node_id not in self.graph:
            return []
        try:
            return list(nx.descendants(self.graph, node_id))
        except nx.NetworkXError:
            logger.warning("Error calculating descendants for %s", node_id)
            return []
    
    def get_all_dependents(self, node_id: str) -> List[str]:
        """Get all transitive dependents of a package."""
        if node_id not in self.graph:
            return []
        try:
            return list(nx.ancestors(self.graph, node_id))
        except nx.NetworkXError:
            logger.warning("Error calculating ancestors for %s", node_id)
            return []
    
    def get_dependency_info(self, node_id: str) -> Dict[str, Any]:
        """Get comprehensive dependency information for a package."""
        if node_id not in self.graph:
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
        if node_id not in self.graph:
            return {}
        return dict(self.graph.nodes[node_id])
    
    def get_shortest_path(self, source: str, target: str) -> List[str]:
        """Get shortest dependency path between two packages."""
        if source not in self.graph or target not in self.graph:
            return []
        try:
            return nx.shortest_path(self.graph, source, target)
        except nx.NetworkXNoPath:
            return []
        except nx.NetworkXError:
            logger.warning("Error calculating path from %s to %s", source, target)
            return []
    
    def find_circular_dependencies(self) -> List[List[str]]:
        """Find circular dependency cycles in the graph."""
        try:
            cycles = list(nx.simple_cycles(self.graph))
            if cycles:
                logger.warning("Found %d circular dependency cycles", len(cycles))
            return cycles
        except Exception as e:
            logger.error("Error finding circular dependencies: %s", e)
            return []
    
    def get_graph_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the dependency graph."""
        num_nodes = self.graph.number_of_nodes()
        num_edges = self.graph.number_of_edges()
        
        # Calculate connectivity stats
        try:
            strongly_connected = len(list(nx.strongly_connected_components(self.graph)))
            weakly_connected = len(list(nx.weakly_connected_components(self.graph)))
        except Exception as e:
            logger.warning("Error calculating connectivity: %s", e)
            strongly_connected = 0
            weakly_connected = 0
        
        # Calculate degree statistics
        in_degrees = [d for n, d in self.graph.in_degree()]
        out_degrees = [d for n, d in self.graph.out_degree()]
        
        return {
            "total_packages": num_nodes,
            "total_dependencies": num_edges,
            "strongly_connected_components": strongly_connected,
            "weakly_connected_components": weakly_connected,
            "average_dependencies_per_package": sum(out_degrees) / num_nodes if num_nodes > 0 else 0,
            "average_dependents_per_package": sum(in_degrees) / num_nodes if num_nodes > 0 else 0,
            "max_dependencies": max(out_degrees) if out_degrees else 0,
            "max_dependents": max(in_degrees) if in_degrees else 0,
            "packages_with_no_dependencies": sum(1 for d in out_degrees if d == 0),
            "packages_with_no_dependents": sum(1 for d in in_degrees if d == 0)
        }
    
    def export_graph(self, output_path: str, format: str = "gexf") -> None:
        """Export the dependency graph to various formats for external analysis."""
        try:
            if format.lower() == "gexf":
                nx.write_gexf(self.graph, output_path)
            elif format.lower() == "graphml":
                nx.write_graphml(self.graph, output_path)
            elif format.lower() == "edgelist":
                nx.write_edgelist(self.graph, output_path)
            else:
                raise ValueError(f"Unsupported format: {format}")
            
            logger.info("Exported dependency graph to %s (format: %s)", output_path, format)
        except Exception as e:
            logger.error("Error exporting graph: %s", e)
    
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
        
        for node_id in self.graph.graph.nodes():
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