#!/usr/bin/env python3
"""
CDP DOM Performance Test

Connects to an existing Chrome instance via CDP and measures DOM building performance.
"""

import asyncio
import time
import json
import requests
from pathlib import Path
from playwright.async_api import async_playwright

class CDPDOMTester:
    def __init__(self, port=9222):
        self.port = port
        self.cdp_url = None
        self.browser = None
        self.page = None
        self.scripts = {}
    
    def discover_cdp_endpoint(self):
        """Discover CDP WebSocket URL from the running Chrome instance"""
        try:
            # Get CDP endpoint from Chrome's JSON API
            response = requests.get(f"http://localhost:{self.port}/json/version", timeout=5)
            if response.status_code == 200:
                data = response.json()
                self.cdp_url = data['webSocketDebuggerUrl']
                print(f"✓ Found CDP endpoint: {self.cdp_url}")
                return True
            else:
                print(f"❌ Failed to connect to Chrome on port {self.port}")
                return False
        except requests.exceptions.ConnectionError:
            print(f"❌ No Chrome instance found on port {self.port}")
            print(f"   Start Chrome with: chrome --remote-debugging-port={self.port}")
            return False
        except Exception as e:
            print(f"❌ Error discovering CDP endpoint: {e}")
            return False
    
    def _load_scripts(self):
        """Load DOM building JavaScript files"""
        current_dir = Path(__file__).parent
        browser_use_dom = current_dir.parent / "dom"
        
        # Load original script
        original_script_path = browser_use_dom / "buildDomTree-top.js"
        if original_script_path.exists():
            self.scripts['original'] = original_script_path.read_text()
            print("✓ Loaded original buildDomTree-top.js")
        else:
            print("⚠ Original buildDomTree-top.js not found")
            
        # Load optimized script
        optimized_script_path = browser_use_dom / "buildDomTree-optimized.js"
        if optimized_script_path.exists():
            self.scripts['optimized'] = optimized_script_path.read_text()
            print("✓ Loaded buildDomTree-optimized.js")
        else:
            print("⚠ Optimized buildDomTree-optimized.js not found")
    
    async def connect(self):
        """Connect to the Chrome instance via CDP"""
        if not self.discover_cdp_endpoint():
            return False
        
        self._load_scripts()
        
        if not self.scripts:
            print("❌ No DOM scripts found!")
            return False
        
        try:
            # Connect to existing Chrome instance
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
            
            # Get the active page or create a new one
            contexts = self.browser.contexts
            if contexts:
                pages = contexts[0].pages
                if pages:
                    self.page = pages[0]
                    print(f"✓ Connected to existing page: {self.page.url}")
                else:
                    self.page = await contexts[0].new_page()
                    print("✓ Created new page")
            else:
                context = await self.browser.new_context()
                self.page = await context.new_page()
                print("✓ Created new context and page")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to connect via CDP: {e}")
            return False
    
    async def test_current_page(self, iterations=3):
        """Test DOM building performance on the current page"""
        if not self.page:
            print("❌ Not connected to a page")
            return None
        
        current_url = self.page.url
        print(f"\nTesting current page: {current_url}")
        print("=" * 60)
        
        # Test arguments
        args = {
            'doHighlightElements': True,
            'focusHighlightIndex': -1,
            'viewportExpansion': 0,
        }
        
        results = {}
        dom_outputs = {}  # Store DOM outputs for correctness comparison
        
        # Test each script
        for script_name, script_code in self.scripts.items():
            print(f"\nTesting {script_name}...")
            times = []
            element_count = 0
            interactive_count = 0
            last_result = None
            
            for i in range(iterations):
                # Clear highlights
                await self._clear_highlights()
                
                # Measure execution time
                start_time = time.perf_counter()
                
                try:
                    result = await self.page.evaluate(script_code, args)
                    end_time = time.perf_counter()
                    
                    execution_time = (end_time - start_time) * 1000  # ms
                    times.append(execution_time)
                    
                    print(f"  Run {i+1}: {execution_time:.2f}ms")
                    
                    # Store last result for correctness comparison
                    last_result = result
                    
                    # Count elements on first run
                    if i == 0:
                        element_count = self._count_elements(result)
                        interactive_count = self._count_interactive_elements(result)
                        
                except Exception as e:
                    print(f"  Run {i+1}: ERROR - {e}")
                    continue
            
            if times:
                avg_time = sum(times) / len(times)
                min_time = min(times)
                max_time = max(times)
                
                results[script_name] = {
                    'avg_ms': avg_time,
                    'min_ms': min_time,
                    'max_ms': max_time,
                    'times': times,
                    'elements': element_count,
                    'interactive': interactive_count
                }
                
                # Store the last DOM output for correctness comparison
                dom_outputs[script_name] = last_result
        
        # Perform correctness comparison
        if len(dom_outputs) >= 2:
            self._compare_dom_correctness(dom_outputs)
        
        # Print performance comparison
        self._print_results(results, current_url)
        return results
    
    async def navigate_and_test(self, url, iterations=3):
        """Navigate to a URL and test DOM building performance"""
        if not self.page:
            print("❌ Not connected to a page")
            return None
        
        print(f"\nNavigating to: {url}")
        try:
            await self.page.goto(url)
            await self.page.wait_for_load_state('networkidle')
            print("✓ Page loaded")
        except Exception as e:
            print(f"❌ Failed to load page: {e}")
            return None
        
        return await self.test_current_page(iterations)
    
    def _compare_dom_correctness(self, dom_outputs):
        """Compare DOM outputs for correctness between different scripts"""
        print(f"\n{'='*60}")
        print("DOM CORRECTNESS COMPARISON")
        print(f"{'='*60}")
        
        script_names = list(dom_outputs.keys())
        if len(script_names) < 2:
            print("❌ Need at least 2 script outputs to compare")
            return
        
        # Compare all pairs
        comparisons = []
        for i in range(len(script_names)):
            for j in range(i + 1, len(script_names)):
                script1, script2 = script_names[i], script_names[j]
                dom1, dom2 = dom_outputs[script1], dom_outputs[script2]
                
                comparison = self._deep_compare_dom(dom1, dom2, script1, script2)
                comparisons.append((script1, script2, comparison))
        
        # Print comparison results
        for script1, script2, comparison in comparisons:
            print(f"\n{script1} vs {script2}:")
            if comparison['identical']:
                print(f"  ✅ DOM structures are identical")
            else:
                print(f"  ❌ DOM structures differ:")
                for diff in comparison['differences']:
                    print(f"     • {diff}")
            
            print(f"  📊 Statistics:")
            print(f"     • Elements compared: {comparison['elements_compared']}")
            print(f"     • Differences found: {comparison['diff_count']}")
    
    def _deep_compare_dom(self, dom1, dom2, name1="DOM1", name2="DOM2"):
        """Deep comparison of two DOM structures"""
        differences = []
        elements_compared = 0
        
        def compare_nodes(node1, node2, path="root"):
            nonlocal elements_compared, differences
            elements_compared += 1
            
            # Compare node types
            if type(node1) != type(node2):
                differences.append(f"{path}: Type mismatch ({type(node1).__name__} vs {type(node2).__name__})")
                return
            
            if not isinstance(node1, dict) or not isinstance(node2, dict):
                if node1 != node2:
                    differences.append(f"{path}: Value mismatch ({repr(node1)} vs {repr(node2)})")
                return
            
            # Compare tag names
            tag1, tag2 = node1.get('tagName'), node2.get('tagName')
            if tag1 != tag2:
                differences.append(f"{path}: Tag mismatch ({tag1} vs {tag2})")
            
            # Compare key attributes (excluding volatile ones)
            attrs1 = node1.get('attributes', {})
            attrs2 = node2.get('attributes', {})
            
            # Skip volatile attributes that might differ between runs
            volatile_attrs = {'browser-user-highlight-id', 'data-playwright-target'}
            stable_attrs1 = {k: v for k, v in attrs1.items() if k not in volatile_attrs}
            stable_attrs2 = {k: v for k, v in attrs2.items() if k not in volatile_attrs}
            
            if stable_attrs1 != stable_attrs2:
                attr_diffs = []
                all_keys = set(stable_attrs1.keys()) | set(stable_attrs2.keys())
                for key in all_keys:
                    val1, val2 = stable_attrs1.get(key), stable_attrs2.get(key)
                    if val1 != val2:
                        attr_diffs.append(f"{key}: {repr(val1)} vs {repr(val2)}")
                
                if attr_diffs:
                    differences.append(f"{path}: Attribute differences: {', '.join(attr_diffs)}")
            
            # Compare interactive/visible flags
            for flag in ['isInteractive', 'isVisible', 'isTopElement']:
                val1, val2 = node1.get(flag), node2.get(flag)
                if val1 != val2:
                    differences.append(f"{path}: {flag} mismatch ({val1} vs {val2})")
            
            # Compare children counts first
            children1 = node1.get('children', [])
            children2 = node2.get('children', [])
            
            if len(children1) != len(children2):
                differences.append(f"{path}: Children count mismatch ({len(children1)} vs {len(children2)})")
                # Still compare what we can
                min_children = min(len(children1), len(children2))
                for i in range(min_children):
                    compare_nodes(children1[i], children2[i], f"{path}/child[{i}]")
            else:
                # Compare all children
                for i, (child1, child2) in enumerate(zip(children1, children2)):
                    compare_nodes(child1, child2, f"{path}/child[{i}]")
        
        # Start comparison
        compare_nodes(dom1, dom2)
        
        return {
            'identical': len(differences) == 0,
            'differences': differences[:10],  # Limit to first 10 differences
            'diff_count': len(differences),
            'elements_compared': elements_compared
        }
    
    def _print_results(self, results, page_url=""):
        """Print formatted performance results"""
        print(f"\n{'='*60}")
        print(f"PERFORMANCE COMPARISON - {page_url}")
        print(f"{'='*60}")
        
        for script_name, stats in results.items():
            print(f"{script_name:>12}: {stats['avg_ms']:>8.2f}ms avg "
                  f"({stats['min_ms']:.2f}-{stats['max_ms']:.2f}ms) "
                  f"| {stats['elements']} elements "
                  f"| {stats['interactive']} interactive")
        
        # Calculate improvement
        if 'original' in results and 'optimized' in results:
            orig_avg = results['original']['avg_ms']
            opt_avg = results['optimized']['avg_ms']
            
            if orig_avg > 0:
                improvement = ((orig_avg - opt_avg) / orig_avg) * 100
                speedup = orig_avg / opt_avg
                
                print(f"\n{'Improvement':>12}: {improvement:>8.1f}% faster "
                      f"({speedup:.1f}x speedup)")
                
                if improvement > 0:
                    print(f"{'Time Saved':>12}: {orig_avg - opt_avg:>8.2f}ms per execution")
                else:
                    print(f"{'Time Lost':>12}: {opt_avg - orig_avg:>8.2f}ms per execution")
    
    async def _clear_highlights(self):
        """Clear any existing DOM highlights"""
        await self.page.evaluate("""
            const container = document.getElementById('playwright-highlight-container');
            if (container) container.remove();
            // Remove highlight attributes
            document.querySelectorAll('[browser-user-highlight-id]').forEach(el => {
                el.removeAttribute('browser-user-highlight-id');
            });
        """)
    
    def _count_elements(self, node, count=0):
        """Count total elements in DOM tree"""
        if not node or not isinstance(node, dict):
            return count
            
        if node.get('tagName'):
            count += 1
            
        for child in node.get('children', []):
            count = self._count_elements(child, count)
            
        return count
    
    def _count_interactive_elements(self, node, count=0):
        """Count interactive elements in DOM tree"""
        if not node or not isinstance(node, dict):
            return count
            
        if node.get('isInteractive'):
            count += 1
            
        for child in node.get('children', []):
            count = self._count_interactive_elements(child, count)
            
        return count
    
    async def cleanup(self):
        """Clean up resources"""
        if self.browser:
            # Don't close the browser since we connected to an existing instance
            await self.browser.close()
        print("✓ Disconnected from Chrome")

async def main():
    """Main execution"""
    import sys
    
    # Parse command line arguments: port [url] [iterations]
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    test_url = sys.argv[2] if len(sys.argv) > 2 else None
    iterations = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    
    print(f"CDP DOM Performance Test")
    print(f"Port: {port}")
    if test_url:
        print(f"Test URL: {test_url}")
    print(f"Iterations: {iterations}")
    print("=" * 60)
    
    tester = CDPDOMTester(port)
    
    try:
        # Connect to Chrome instance
        if not await tester.connect():
            print("\n💡 To start Chrome with remote debugging:")
            print(f"   chrome --remote-debugging-port={port} --user-data-dir=/tmp/chrome-debug")
            return
        
        if test_url:
            # Navigate to specific URL and test
            await tester.navigate_and_test(test_url, iterations)
        else:
            # Test current page
            await tester.test_current_page(iterations)
        
    except KeyboardInterrupt:
        print("\n🛑 Test interrupted by user")
    except Exception as e:
        print(f"❌ Test failed: {e}")
    finally:
        await tester.cleanup()

if __name__ == "__main__":
    asyncio.run(main()) 