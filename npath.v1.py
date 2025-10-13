import asyncio
from scapy.all import IP, TCP, sr1, conf
import networkx as nx
import plotly.graph_objects as go
from concurrent.futures import ThreadPoolExecutor
import time
import statistics
import os
import sys
import argparse
import socket
import numpy as np
from itertools import groupby


class AdvancedNetworkPathAnalyzer:
    def __init__(self, num_probes, max_paths=5):
        self.graph = nx.DiGraph()
        self.source_ip = self.get_source_ip()
        self.num_probes = num_probes
        self.max_paths = max_paths
        self.destination_ip = None
        print(f"Initialized analyzer with source IP: {self.source_ip}")

    def get_source_ip(self):
        return conf.route.route("0.0.0.0")[1]

    async def discover_paths(self, destination, max_hops=30):
        try:
            self.destination_ip = socket.gethostbyname(destination)
        except socket.gaierror:
            print(f"Unable to resolve hostname: {destination}")
            return []

        print(f"\nStarting path discovery to {destination} ({self.destination_ip})")
        print(f"Maximum hops: {max_hops}, Probes per hop: {self.num_probes}")
        print("-" * 50)

        paths = []
        self.graph.add_node(self.source_ip)

        async def probe_path():
            path = [self.source_ip]
            prev_hop = self.source_ip
            destination_reached = False

            for ttl in range(1, max_hops + 1):
                print(f"\nProbing TTL {ttl:2d}: ", end='', flush=True)
                hop, metrics = await self.probe_hop(self.destination_ip, ttl)
                if hop:
                    if hop != prev_hop:  # Avoid adding duplicate hops
                        path.append(hop)
                        if hop not in self.graph:
                            self.graph.add_node(hop)
                        if not self.graph.has_edge(prev_hop, hop):
                            self.graph.add_edge(prev_hop, hop, **metrics)
                        response_rate = (metrics['responses'] / metrics['probes']) * 100
                        print(f"Found {hop:15s} | RTT: {metrics['avg_rtt']:6.2f}ms | Response Rate: {response_rate:5.1f}%")
                        prev_hop = hop

                    if hop == self.destination_ip:
                        print("\nDestination reached!")
                        destination_reached = True
                        break
                else:
                    print("No response")

            if not destination_reached and path[-1] != self.destination_ip:
                print(f"\nAdding destination {self.destination_ip} to path")
                path.append(self.destination_ip)
                self.graph.add_node(self.destination_ip)
                self.graph.add_edge(prev_hop, self.destination_ip, avg_rtt=None, min_rtt=None, max_rtt=None, responses=0, probes=self.num_probes)

            return path

        print(f"Attempting to discover up to {self.max_paths} unique paths...")
        tasks = [probe_path() for _ in range(self.max_paths)]
        paths = await asyncio.gather(*tasks)

        # Remove duplicate paths and sort by length
        unique_paths = sorted(list(set(tuple(self.remove_consecutive_duplicates(path)) for path in paths)), key=len)

        print(f"\nDiscovered {len(unique_paths)} unique paths")
        for i, path in enumerate(unique_paths, 1):
            print(f"\nPath {i}: {' -> '.join(path)}")

        return unique_paths

    async def probe_hop(self, destination_ip, ttl):
        rtts = []
        responses = 0
        last_responsive_hop = None
        
        for i in range(self.num_probes):
            start_time = time.time()
            try:
                pkt = IP(dst=destination_ip, ttl=ttl) / TCP(dport=80, flags="S")
                reply = await self.async_sr1(pkt, timeout=2)
                if reply:
                    responses += 1
                    rtt = (time.time() - start_time) * 1000  # Convert to milliseconds
                    rtts.append(rtt)
                    last_responsive_hop = reply.src
                    print(".", end='', flush=True)
                    if reply.haslayer(TCP):
                        if reply[TCP].flags & 0x12 or reply[TCP].flags & 0x14:  # SYN-ACK or RST-ACK
                            break  # Exit loop early if we get a TCP response
                else:
                    print("x", end='', flush=True)
            except Exception as e:
                print(f"Error probing hop {ttl}: {e}")
        
        print(" ", end='', flush=True)  # Add a space after the progress indicators
        
        if responses > 0 and last_responsive_hop:
            return last_responsive_hop, {
                'avg_rtt': statistics.mean(rtts),
                'min_rtt': min(rtts),
                'max_rtt': max(rtts),
                'responses': responses,
                'probes': self.num_probes
            }
        return None, {'avg_rtt': None, 'min_rtt': None, 'max_rtt': None, 'responses': 0, 'probes': self.num_probes}

    def remove_consecutive_duplicates(self, path):
        return [x[0] for x in groupby(path)]

    async def async_sr1(self, packet, timeout=2):
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            try:
                result = await loop.run_in_executor(pool, lambda: sr1(packet, timeout=timeout, verbose=0))
                return result
            except Exception as e:
                print(f"Error in async_sr1: {e}")
                return None

    async def discover_network(self, destination_ip):
        print("Starting network discovery...")
        paths = await self.discover_paths(destination_ip)
        print("Network discovery complete.")
        return paths

    def analyze_paths(self, destination):
        print(f"Analyzing paths from {self.source_ip} to {destination}...")
        try:
            all_paths = list(nx.all_simple_paths(self.graph, self.source_ip, destination))
            print(f"Found {len(all_paths)} path(s)")
            return all_paths
        except nx.NodeNotFound:
            print(f"Error: Either source ({self.source_ip}) or destination ({destination}) not found in the graph.")
            return []

    def visualize_network(self, paths, destination):
        if not paths:
            print("No paths to visualize.")
            return
        
        print("Generating interactive network visualization...")
        
        # Create a custom layout for the graph
        pos = self.create_custom_layout(paths)
        
        # Prepare node traces
        node_trace = self.prepare_node_trace(pos)

        # Prepare edge traces
        edge_traces = self.prepare_edge_traces(paths, pos)

        # Create the figure
        fig = go.Figure(data=[*edge_traces, node_trace],
             layout=go.Layout(
                title=f'Network Path from {self.source_ip} to {destination}',
                titlefont_size=16,
                showlegend=False,
                hovermode='closest',
                margin=dict(b=20,l=5,r=5,t=40),
                plot_bgcolor='white',
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                height=600,
                width=1200)
                )

        # Add source and destination labels
        fig.add_annotation(x=pos[self.source_ip][0], y=pos[self.source_ip][1]-0.1,
                           text="Source", showarrow=False, yshift=-40, font=dict(size=14, color="black"))
        fig.add_annotation(x=pos[self.destination_ip][0], y=pos[self.destination_ip][1]-0.1,
                           text="Destination", showarrow=False, yshift=-40, font=dict(size=14, color="black"))

        # Save the interactive plot as an HTML file
        output_file = 'network_path_analysis.html'
        fig.write_html(output_file)
        
        print(f"Interactive network visualization saved as '{output_file}'")
        print(f"You can find the file at: {os.path.abspath(output_file)}")
        print("Open this file in a web browser to interact with the visualization.")

    def create_custom_layout(self, paths):
        pos = {}
        max_path_length = max(len(path) for path in paths)
        
        # Position nodes horizontally
        for path in paths:
            for i, node in enumerate(path):
                if node not in pos:
                    pos[node] = [i / (max_path_length - 1), 0]

        # Adjust vertical positions for branching paths
        vertical_spacing = 0.1
        for i, path in enumerate(paths):
            y_offset = 0 if i == 0 else ((-1)**i) * (i//2 + 1) * vertical_spacing
            for node in path:
                pos[node][1] += y_offset

        return {node: tuple(coord) for node, coord in pos.items()}

    def prepare_node_trace(self, pos):
        node_x, node_y, node_text, node_size = [], [], [], []
        for node in self.graph.nodes():
            x, y = pos[node]
            node_x.append(x)
            node_y.append(y)
            node_text.append(node)
            node_size.append(30 if node in [self.source_ip, self.destination_ip] else 20)
        
        node_trace = go.Scatter(
            x=node_x, y=node_y,
            mode='markers+text',
            hoverinfo='text',
            text=node_text,
            textposition="bottom center",
            textfont=dict(size=10, color="black"),
            marker=dict(
                showscale=False,
                color='lightgreen',
                size=node_size,
                line=dict(width=2, color='darkgreen'))
        )
        return node_trace

    def prepare_edge_traces(self, paths, pos):
        edge_traces = []
        for path in paths:
            edge_x, edge_y, edge_text = [], [], []
            for start, end in zip(path[:-1], path[1:]):
                x0, y0 = pos[start]
                x1, y1 = pos[end]
                edge_x.extend([x0, x1, None])
                edge_y.extend([y0, y1, None])
                
                edge_data = self.graph.get_edge_data(start, end)
                avg_rtt = edge_data.get('avg_rtt')
                latency = f"{avg_rtt:.0f}ms" if avg_rtt is not None else "N/A"
                edge_text.append(latency)

            edge_trace = go.Scatter(
                x=edge_x, y=edge_y,
                line=dict(width=2, color='lightgreen'),
                hoverinfo='none',
                mode='lines',
            )
            edge_traces.append(edge_trace)

            # Add edge labels
            label_trace = go.Scatter(
                x=[(x1 + x2) / 2 for x1, x2 in zip(edge_x[::3], edge_x[1::3])],
                y=[(y1 + y2) / 2 + 0.02 for y1, y2 in zip(edge_y[::3], edge_y[1::3])],
                mode='text',
                text=edge_text,
                textposition='top center',
                textfont=dict(size=10, color="darkgreen"),
                hoverinfo='none'
            )
            edge_traces.append(label_trace)

        return edge_traces

async def main():
    parser = argparse.ArgumentParser(description="Analyze network path to a destination IP.")
    parser.add_argument("-d", "--destination", help="The destination address/host to analyze")
    parser.add_argument("-p", "--probes", type=int, default=3, help="Number of probes per hop (default: 3)")
    parser.add_argument("-m", "--max-hops", type=int, default=10, help="Maximum number of hops (default: 30)")
    parser.add_argument("-n", "--max-paths", type=int, default=10, help="Maximum number of paths to discover (default: 5)")
    args = parser.parse_args()

    print("Initializing Advanced Network Path Analyzer...")
    analyzer = AdvancedNetworkPathAnalyzer(num_probes=args.probes, max_paths=args.max_paths)
    
    print(f"\nDiscovering network paths from {analyzer.source_ip} to {args.destination}...")
    paths = await analyzer.discover_network(args.destination)
    
    if paths:
        print(f"\nDiscovered {len(paths)} possible path(s):")
        for i, path in enumerate(paths, 1):
            print(f"Path {i}: {' -> '.join(path)}")
        
        analyzer.visualize_network(paths, args.destination)
    else:
        print("Unable to discover any paths to the destination.")

    print("\nAnalysis complete.")

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())