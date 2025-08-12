# CDP DOM Performance Testing Harness

A minimal testing harness for measuring DOM building performance by connecting to an existing Chrome instance via Chrome DevTools Protocol (CDP). This allows testing with real browser conditions and your actual browsing sessions.

## Features

- **CDP Connection**: Connects to existing Chrome instances via remote debugging
- **Real Browser Testing**: Tests with actual browser conditions and extensions
- **Live Page Testing**: Can test the currently open page without navigation
- **Minimal Dependencies**: Only requires Playwright and requests
- **Performance Metrics**: Measures execution time, element counts, and improvements
- **Easy Setup**: Helper scripts to start Chrome with debugging enabled

## Setup

### 1. Install Dependencies

```bash
cd browser_service/dom-load-test
pip install -r requirements.txt
```

### 2. Ensure Scripts Exist

The harness automatically loads:
- `../../browser_use/dom/buildDomTree-top.js` (original)
- `../../browser_use/dom/buildDomTree-optimized.js` (optimized)

Make sure both files exist in the `browser_use/dom/` directory.

### 3. Start Chrome with Remote Debugging

**Option A: Use the helper script (recommended)**
```bash
./start_chrome_debug.sh
```

**Option B: Manual Chrome startup**
```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-debug

# Linux
google-chrome \
    --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-debug
```

## Usage

### Test Current Page

Connect to Chrome and test whatever page is currently open:

```bash
python cdp_dom_test.py
```

### Test with Custom Port

```bash
python cdp_dom_test.py 9223
```

### Navigate and Test

Navigate to a specific URL and test:

```bash
python cdp_dom_test.py 9222 "https://github.com"
```

### Custom Iterations

```bash
python cdp_dom_test.py 9222 "https://stackoverflow.com" 5
```

## Example Workflow

```bash
# 1. Start Chrome with debugging
./start_chrome_debug.sh

# 2. Navigate to your target page in the browser (manually)
# 3. Test the current page
python cdp_dom_test.py

# OR navigate programmatically
python cdp_dom_test.py 9222 "https://your-complex-app.com"
```

## Example Output

```
CDP DOM Performance Test
Port: 9222
Iterations: 3
============================================================
✓ Found CDP endpoint: ws://localhost:9222/devtools/browser/...
✓ Loaded original buildDomTree-top.js
✓ Loaded buildDomTree-optimized.js
✓ Connected to existing page: https://github.com

Testing current page: https://github.com
============================================================

Testing original...
  Run 1: 1247.83ms
  Run 2: 1198.45ms
  Run 3: 1223.67ms

Testing optimized...
  Run 1: 127.42ms
  Run 2: 119.38ms
  Run 3: 124.91ms

============================================================
PERFORMANCE COMPARISON - https://github.com
============================================================
    original:  1223.32ms avg (1198.45-1247.83ms) | 2847 elements | 156 interactive
   optimized:   123.90ms avg (119.38-127.42ms) | 2847 elements | 156 interactive

 Improvement:      89.9% faster (9.9x speedup)
  Time Saved:    1099.42ms per execution

✓ Disconnected from Chrome
```

## Advantages of CDP Testing

### Real Browser Conditions
- Tests with your actual browser profile and extensions
- Includes real network conditions and caching
- Uses actual browser rendering and layout engines

### Live Testing
- Test pages you're already browsing
- No need to navigate programmatically
- Can test authenticated pages or complex application states

### Debugging Friendly
- Keep DevTools open while testing
- See actual DOM highlights in the browser
- Can inspect performance with browser profiler

## Helper Scripts

### `start_chrome_debug.sh`

Automatically finds and starts Chrome with remote debugging:

```bash
# Default port 9222
./start_chrome_debug.sh

# Custom port
./start_chrome_debug.sh 9223

# Custom port and user data directory
./start_chrome_debug.sh 9223 /tmp/my-chrome-debug
```

Features:
- Auto-detects Chrome installation (macOS, Linux)
- Creates isolated user data directory
- Tests CDP connection
- Provides usage instructions

## Troubleshooting

### Connection Issues

**"No Chrome instance found on port 9222"**
- Make sure Chrome is started with `--remote-debugging-port=9222`
- Check if another process is using port 9222: `lsof -i :9222`

**"Failed to connect via CDP"**
- Ensure Chrome has fully started (wait 5-10 seconds)
- Try refreshing the page in Chrome
- Check Chrome console for errors

### Performance Issues

**Very slow execution times**
- Complex pages with many elements will be slower
- Try testing with `doHighlightElements: false` for pure performance
- Clear browser cache and extensions if needed

### Script Loading Issues

**"DOM scripts not found"**
- Ensure `buildDomTree-top.js` and `buildDomTree-optimized.js` exist
- Check file paths are correct relative to the test script

## Advanced Usage

### Testing Authenticated Pages

1. Start Chrome with debugging
2. Manually log into your application
3. Navigate to the target page
4. Run the test on the current page

### Testing with Extensions

The CDP connection preserves your browser profile, so extensions remain active during testing. This provides more realistic performance measurements.

### Profiling Mode

To see detailed performance breakdown:

1. Open Chrome DevTools
2. Go to Performance tab
3. Start recording
4. Run the CDP test
5. Stop recording to see detailed performance analysis

### Custom Test Parameters

Modify the test arguments in `cdp_dom_test.py`:

```python
args = {
    'doHighlightElements': False,  # Disable highlighting for pure performance
    'focusHighlightIndex': -1,     # Focus specific element
    'viewportExpansion': 100,      # Expand viewport testing area
}
```

## File Structure

```
dom-load-test/
├── README.md                    # This file
├── requirements.txt             # Python dependencies (playwright + requests)
├── cdp_dom_test.py             # Main CDP testing script
├── start_chrome_debug.sh       # Chrome startup helper script
└── performance_results.json    # Generated test results (if saved)
```

## Integration with Existing Workflows

This harness is perfect for:

- **Development Testing**: Test DOM performance during development
- **CI/CD Integration**: Automated performance regression testing
- **Real-world Validation**: Test on actual production pages
- **Performance Debugging**: Identify specific performance bottlenecks 