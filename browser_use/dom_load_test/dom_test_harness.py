#!/usr/bin/env python3
"""
DOM Performance Testing Harness

Minimal test harness for measuring DOM building performance with only Playwright dependency.
Tests both original and optimized buildDomTree implementations.
"""

import asyncio
import time
import json
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from playwright.async_api import async_playwright, Page, Browser

class DOMTestHarness:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.scripts: Dict[str, str] = {}
        
    async def setup(self):
        """Initialize Playwright and load scripts"""
        print("Setting up DOM test harness...")
        
        # Load DOM building scripts
        self._load_scripts()
        
        # Start browser
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        
        print("✓ Browser initialized")
        
    def _load_scripts(self):
        """Load DOM building JavaScript files"""
        current_dir = Path(__file__).parent
        browser_use_dom = current_dir.parent.parent / "browser_use" / "dom"
        
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
    
    async def load_test_page(self, url: str = "https://example.com"):
        """Load a test page"""
        print(f"Loading test page: {url}")
        await self.page.goto(url)
        await self.page.wait_for_load_state('networkidle')
        print("✓ Page loaded")
    
    async def measure_dom_build_performance(
        self, 
        script_name: str, 
        iterations: int = 5,
        highlight_elements: bool = True,
        focus_element: int = -1,
        viewport_expansion: int = 0
    ) -> Dict:
        """Measure DOM building performance for a specific script"""
        
        if script_name not in self.scripts:
            raise ValueError(f"Script '{script_name}' not found")
            
        script = self.scripts[script_name]
        args = {
            'doHighlightElements': highlight_elements,
            'focusHighlightIndex': focus_element,
            'viewportExpansion': viewport_expansion,
        }
        
        print(f"\nTesting {script_name} script ({iterations} iterations)...")
        
        times = []
        results = []
        
        for i in range(iterations):
            # Clear any existing highlights
            await self._clear_highlights()
            
            # Measure execution time
            start_time = time.perf_counter()
            
            try:
                result = await self.page.evaluate(script, args)
                end_time = time.perf_counter()
                
                execution_time = (end_time - start_time) * 1000  # Convert to milliseconds
                times.append(execution_time)
                results.append(result)
                
                print(f"  Iteration {i+1}: {execution_time:.2f}ms")
                
            except Exception as e:
                print(f"  Iteration {i+1}: ERROR - {e}")
                continue
        
        if not times:
            return {"error": "All iterations failed"}
            
        # Calculate statistics
        stats = {
            "script": script_name,
            "iterations": len(times),
            "times_ms": times,
            "avg_ms": statistics.mean(times),
            "min_ms": min(times),
            "max_ms": max(times),
            "std_dev_ms": statistics.stdev(times) if len(times) > 1 else 0,
            "median_ms": statistics.median(times),
        }
        
        # Analyze the last result
        if results:
            last_result = results[-1]
            stats.update({
                "element_count": self._count_elements(last_result),
                "interactive_count": self._count_interactive_elements(last_result),
                "result_size_chars": len(json.dumps(last_result)) if last_result else 0,
            })
        
        return stats
    
    def _count_elements(self, node, count=0):
        """Recursively count elements in DOM tree"""
        if not node or not isinstance(node, dict):
            return count
            
        if node.get('tagName'):
            count += 1
            
        for child in node.get('children', []):
            count = self._count_elements(child, count)
            
        return count
    
    def _count_interactive_elements(self, node, count=0):
        """Recursively count interactive elements in DOM tree"""
        if not node or not isinstance(node, dict):
            return count
            
        if node.get('isInteractive'):
            count += 1
            
        for child in node.get('children', []):
            count = self._count_interactive_elements(child, count)
            
        return count
    
    async def _clear_highlights(self):
        """Clear any existing DOM highlights"""
        await self.page.evaluate("""
            const container = document.getElementById('playwright-highlight-container');
            if (container) {
                container.remove();
            }
            // Remove highlight attributes
            document.querySelectorAll('[browser-user-highlight-id]').forEach(el => {
                el.removeAttribute('browser-user-highlight-id');
            });
        """)
    
    async def compare_performance(
        self, 
        iterations: int = 5,
        test_configs: List[Dict] = None
    ) -> Dict:
        """Compare performance between original and optimized scripts"""
        
        if test_configs is None:
            test_configs = [
                {"highlight_elements": True, "viewport_expansion": 0},
                {"highlight_elements": False, "viewport_expansion": 0},
                {"highlight_elements": True, "viewport_expansion": 100},
            ]
        
        comparison_results = {}
        
        for config in test_configs:
            config_name = f"highlight_{config['highlight_elements']}_viewport_{config['viewport_expansion']}"
            print(f"\n{'='*60}")
            print(f"Testing configuration: {config_name}")
            print(f"{'='*60}")
            
            config_results = {}
            
            for script_name in self.scripts.keys():
                try:
                    result = await self.measure_dom_build_performance(
                        script_name, 
                        iterations=iterations,
                        **config
                    )
                    config_results[script_name] = result
                except Exception as e:
                    print(f"Error testing {script_name}: {e}")
                    config_results[script_name] = {"error": str(e)}
            
            # Calculate performance improvement
            if 'original' in config_results and 'optimized' in config_results:
                orig = config_results['original']
                opt = config_results['optimized']
                
                if 'avg_ms' in orig and 'avg_ms' in opt and orig['avg_ms'] > 0:
                    improvement = ((orig['avg_ms'] - opt['avg_ms']) / orig['avg_ms']) * 100
                    speedup = orig['avg_ms'] / opt['avg_ms']
                    
                    config_results['performance_improvement'] = {
                        "improvement_percent": improvement,
                        "speedup_factor": speedup,
                        "time_saved_ms": orig['avg_ms'] - opt['avg_ms']
                    }
            
            comparison_results[config_name] = config_results
        
        return comparison_results
    
    def print_performance_summary(self, results: Dict):
        """Print a formatted summary of performance results"""
        print(f"\n{'='*80}")
        print("PERFORMANCE SUMMARY")
        print(f"{'='*80}")
        
        for config_name, config_results in results.items():
            print(f"\nConfiguration: {config_name}")
            print("-" * 50)
            
            for script_name, stats in config_results.items():
                if script_name == 'performance_improvement':
                    continue
                    
                if 'error' in stats:
                    print(f"{script_name:>12}: ERROR - {stats['error']}")
                    continue
                
                print(f"{script_name:>12}: {stats['avg_ms']:>8.2f}ms avg "
                      f"({stats['min_ms']:.2f}-{stats['max_ms']:.2f}ms range) "
                      f"| {stats['element_count']} elements "
                      f"| {stats['interactive_count']} interactive")
            
            # Show performance improvement
            if 'performance_improvement' in config_results:
                perf = config_results['performance_improvement']
                print(f"\n{'Improvement':>12}: {perf['improvement_percent']:>8.1f}% faster "
                      f"({perf['speedup_factor']:.1f}x speedup) "
                      f"| -{perf['time_saved_ms']:.2f}ms saved")
    
    async def cleanup(self):
        """Clean up resources"""
        if self.browser:
            await self.browser.close()
        print("✓ Cleanup completed")

async def main():
    """Main test execution"""
    harness = DOMTestHarness()
    
    try:
        await harness.setup()
        
        # Test different pages
        test_pages = [
            "https://example.com",
            "https://github.com",
            "https://stackoverflow.com",
        ]
        
        all_results = {}
        
        for url in test_pages:
            print(f"\n{'#'*80}")
            print(f"TESTING PAGE: {url}")
            print(f"{'#'*80}")
            
            await harness.load_test_page(url)
            
            # Wait a moment for page to settle
            await asyncio.sleep(2)
            
            # Run performance comparison
            results = await harness.compare_performance(iterations=3)
            all_results[url] = results
            
            # Print summary for this page
            harness.print_performance_summary(results)
        
        # Save detailed results to file
        results_file = Path(__file__).parent / "performance_results.json"
        with open(results_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        print(f"\n✓ Detailed results saved to: {results_file}")
        
    except Exception as e:
        print(f"Error during testing: {e}")
    finally:
        await harness.cleanup()

if __name__ == "__main__":
    asyncio.run(main()) 