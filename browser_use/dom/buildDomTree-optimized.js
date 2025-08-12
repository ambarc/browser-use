(
    args = { doHighlightElements: true, focusHighlightIndex: -1, viewportExpansion: 0 }
) => {
    const { doHighlightElements, focusHighlightIndex, viewportExpansion } = args;
    let highlightIndex = 0;

    console.log('focusHighlightIndex:', focusHighlightIndex);

    // Pre-compute viewport boundaries once
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const viewportBounds = {
        top: -viewportExpansion + scrollY,
        left: -viewportExpansion + scrollX,
        bottom: window.innerHeight + viewportExpansion + scrollY,
        right: window.innerWidth + viewportExpansion + scrollX
    };

    // Cache for expensive computations
    const styleCache = new WeakMap();
    const metricsCache = new WeakMap();

    // Pre-defined sets for fast lookups
    const INTERACTIVE_TAGS = new Set([
        'a', 'button', 'details', 'embed', 'input', 'label',
        'menu', 'menuitem', 'object', 'select', 'textarea', 'summary'
    ]);

    const INTERACTIVE_ROLES = new Set([
        'button', 'menu', 'menuitem', 'link', 'checkbox', 'radio',
        'slider', 'tab', 'tabpanel', 'textbox', 'combobox', 'grid',
        'listbox', 'option', 'progressbar', 'scrollbar', 'searchbox',
        'switch', 'tree', 'treeitem', 'spinbutton', 'tooltip', 'a-button-inner', 
        'a-dropdown-button', 'click', 'menuitemcheckbox', 'menuitemradio', 
        'a-button-text', 'button-text', 'button-icon', 'button-icon-only', 
        'button-text-icon-only', 'dropdown', 'combobox'
    ]);

    const IGNORED_TAGS = new Set(['svg', 'script', 'style', 'link', 'meta']);

    function getCachedStyle(element) {
        if (!styleCache.has(element)) {
            styleCache.set(element, window.getComputedStyle(element));
        }
        return styleCache.get(element);
    }

    function getElementMetrics(element) {
        if (!metricsCache.has(element)) {
            const rect = element.getBoundingClientRect();
            const absTop = rect.top + scrollY;
            const absLeft = rect.left + scrollX;
            const absBottom = rect.bottom + scrollY;
            const absRight = rect.right + scrollX;

            metricsCache.set(element, {
                rect,
                absTop, absLeft, absBottom, absRight,
                width: rect.width,
                height: rect.height,
                centerX: rect.left + rect.width / 2,
                centerY: rect.top + rect.height / 2
            });
        }
        return metricsCache.get(element);
    }

    function isInExpandedViewport(metrics) {
        const { absBottom, absTop, absRight, absLeft } = metrics;
        return absBottom >= viewportBounds.top &&
               absTop <= viewportBounds.bottom &&
               absRight >= viewportBounds.left &&
               absLeft <= viewportBounds.right;
    }

    function isElementWorthProcessing(element) {
        // Don't skip based on depth - process all elements
        if (IGNORED_TAGS.has(element.tagName.toLowerCase())) return false;
        return true; // Process all non-ignored elements
    }

    function isShadowRoot(node) {
        if (!node) return false;
        return (
            (typeof ShadowRoot !== 'undefined' && node instanceof ShadowRoot) ||
            (node.nodeType === 11 && node.host !== undefined) ||
            (node.toString && node.toString() === '[object ShadowRoot]') ||
            (node.constructor && node.constructor.name === 'ShadowRoot')
        );
    }

    function isInShadowDOM(element) {
        if (!element) return false;
        if (element.parentNode && isShadowRoot(element.parentNode)) {
            return true;
        }
        const rootNode = element.getRootNode();
        return isShadowRoot(rootNode);
    }

    function isInteractiveFast(element) {
        if (element.tagName.toLowerCase() === 'body') return false;

        const tag = element.tagName.toLowerCase();
        const role = element.getAttribute('role');
        const ariaRole = element.getAttribute('aria-role');
        const tabIndex = element.getAttribute('tabindex');
        
        // Fast tag/role checks
        if (INTERACTIVE_TAGS.has(tag) || INTERACTIVE_ROLES.has(role) || INTERACTIVE_ROLES.has(ariaRole)) {
            return true;
        }

        // Special class checks for known interactive elements
        const classList = element.classList;
        if (classList.contains('pb_c_demogrpahic-drawer') ||
            classList.contains('preferred-pharmacy') ||
            classList.contains('care-team') ||
            classList.contains('preferred-lab') ||
            classList.contains('address-input__container__input')) {
            return true;
        }

        // Fast attribute checks (no DOM traversal)
        if (element.hasAttribute('onclick') ||
            element.hasAttribute('ng-click') ||
            element.hasAttribute('@click') ||
            element.hasAttribute('v-on:click') ||
            element.hasAttribute('data-history-type') ||
            element.getAttribute('data-action') === 'a-dropdown-select' ||
            element.getAttribute('data-action') === 'a-dropdown-button') {
            return true;
        }

        // Quick tabindex check (exclude body parent)
        if (tabIndex !== null && tabIndex !== '-1' && 
            element.parentElement?.tagName.toLowerCase() !== 'body') {
            return true;
        }

        // Check for ARIA properties that suggest interactivity
        if (element.hasAttribute('aria-expanded') ||
            element.hasAttribute('aria-pressed') ||
            element.hasAttribute('aria-selected') ||
            element.hasAttribute('aria-checked')) {
            return true;
        }

        // Check if element is draggable
        if (element.draggable || element.getAttribute('draggable') === 'true') {
            return true;
        }

        // Check for interactive list items
        if (tag === 'li' && element.classList.contains('search-result') && 
            element.hasAttribute('data-id')) {
            return true;
        }

        // Only check cursor style as last resort (requires style computation)
        const style = getCachedStyle(element);
        if (style.cursor === 'pointer') {
            return true;
        }

        // Check for event listeners (simplified)
        if (element.onclick !== null) {
            return true;
        }

        return false;
    }

    function isElementVisibleFast(element) {
        // Special case for target elements
        if (element.classList.contains('pb_c_demogrpahic-drawer') ||
            element.classList.contains('preferred-pharmacy') ||
            element.classList.contains('care-team') ||
            element.classList.contains('preferred-lab')) {
            return true;
        }

        if (isInShadowDOM(element)) {
            return true;
        }

        const metrics = getElementMetrics(element);
        if (metrics.width <= 0 || metrics.height <= 0) return false;

        // Only get style if we need it
        const style = getCachedStyle(element);
        return style.visibility !== 'hidden' && style.display !== 'none';
    }

    function isTopElementSimplified(element) {
        // Special case for target elements
        if (element.classList.contains('pb_c_demogrpahic-drawer') ||
            element.classList.contains('preferred-pharmacy') ||
            element.classList.contains('care-team') ||
            element.classList.contains('preferred-lab')) {
            return true;
        }

        if (isInShadowDOM(element)) {
            return true;
        }

        if (viewportExpansion === -1) return true;

        const metrics = getElementMetrics(element);
        
        // Skip elements completely outside expanded viewport
        if (!isInExpandedViewport(metrics)) {
            return false;
        }

        // Element must have center in viewport to be "top"
        if (metrics.centerX < 0 || metrics.centerX >= window.innerWidth ||
            metrics.centerY < 0 || metrics.centerY >= window.innerHeight) {
            return false;
        }

        // Simple heuristic: if element is visible and has reasonable size, consider it "top"
        return metrics.width >= 1 && metrics.height >= 1;
    }

    function highlightElement(element, index, parentIframe = null) {
        let container = document.getElementById('playwright-highlight-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'playwright-highlight-container';
            container.style.cssText = `
                position: absolute;
                pointer-events: none;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 2147483647;
            `;
            document.body.appendChild(container);
        }

        const colors = [
            '#FF0000', '#00FF00', '#0000FF', '#FFA500',
            '#800080', '#008080', '#FF69B4', '#4B0082',
            '#FF4500', '#2E8B57', '#DC143C', '#4682B4'
        ];
        const color = colors[index % colors.length];
        const bgColor = `${color}1A`;

        const metrics = getElementMetrics(element);
        const { rect } = metrics;

        let top = rect.top + scrollY;
        let left = rect.left + scrollX;

        // Adjust position if element is inside an iframe
        if (parentIframe) {
            const iframeRect = parentIframe.getBoundingClientRect();
            top += iframeRect.top;
            left += iframeRect.left;
        }

        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: absolute;
            border: 2px solid ${color};
            background-color: ${bgColor};
            pointer-events: none;
            box-sizing: border-box;
            top: ${top}px;
            left: ${left}px;
            width: ${rect.width}px;
            height: ${rect.height}px;
        `;

        const label = document.createElement('div');
        const fontSize = Math.min(12, Math.max(8, rect.height / 2));
        const labelWidth = 20;
        const labelHeight = 16;
        
        let labelTop = top + 2;
        let labelLeft = left + rect.width - labelWidth - 2;

        if (rect.width < labelWidth + 4 || rect.height < labelHeight + 4) {
            labelTop = top - labelHeight - 2;
            labelLeft = left + rect.width - labelWidth;
        }

        label.style.cssText = `
            position: absolute;
            background: ${color};
            color: white;
            padding: 1px 4px;
            border-radius: 4px;
            font-size: ${fontSize}px;
            top: ${labelTop}px;
            left: ${labelLeft}px;
        `;
        label.textContent = index;

        container.appendChild(overlay);
        container.appendChild(label);
        element.setAttribute('browser-user-highlight-id', `playwright-highlight-${index}`);
    }

    function getXPathTree(element, stopAtBoundary = true) {
        const segments = [];
        let currentElement = element;

        while (currentElement && currentElement.nodeType === Node.ELEMENT_NODE) {
            // Stop if we hit a shadow root or iframe (when stopAtBoundary is true)
            if (stopAtBoundary && (currentElement.parentNode instanceof ShadowRoot || 
                currentElement.parentNode instanceof HTMLIFrameElement)) {
                break;
            }

            let index = 0;
            let sibling = currentElement.previousSibling;
            while (sibling) {
                if (sibling.nodeType === Node.ELEMENT_NODE &&
                    sibling.nodeName === currentElement.nodeName) {
                    index++;
                }
                sibling = sibling.previousSibling;
            }

            const tagName = currentElement.nodeName.toLowerCase();
            const xpathIndex = index > 0 ? `[${index + 1}]` : '';
            segments.unshift(`${tagName}${xpathIndex}`);

            currentElement = currentElement.parentNode;
        }

        return segments.join('/');
    }

    function isTextNodeVisible(textNode) {
        const range = document.createRange();
        range.selectNodeContents(textNode);
        const rect = range.getBoundingClientRect();

        return rect.width !== 0 &&
            rect.height !== 0 &&
            rect.top >= 0 &&
            rect.top <= window.innerHeight &&
            textNode.parentElement?.checkVisibility({
                checkOpacity: true,
                checkVisibilityCSS: true
            });
    }

    function buildDomTreeOptimized(node, parentIframe = null) {
        if (!node) return null;

        // Handle text nodes quickly
        if (node.nodeType === Node.TEXT_NODE) {
            const textContent = node.textContent.trim();
            if (textContent && isTextNodeVisible(node)) {
                return {
                    type: "TEXT_NODE",
                    text: textContent,
                    isVisible: true
                };
            }
            return null;
        }

        if (node.nodeType !== Node.ELEMENT_NODE) return null;
        
        // Early termination for performance (but no depth limits)
        if (!isElementWorthProcessing(node)) return null;

        const metrics = getElementMetrics(node);
        const isInteractive = isInteractiveFast(node);
        const isVisible = isElementVisibleFast(node);
        const isTop = isTopElementSimplified(node);

        const nodeData = {
            tagName: node.tagName.toLowerCase(),
            attributes: {},
            xpath: getXPathTree(node, true),
            children: [],
            isInteractive,
            isVisible,
            isTopElement: isTop
        };

        // Add complete coordinate information
        nodeData.viewportCoordinates = {
            topLeft: { x: Math.round(metrics.rect.left), y: Math.round(metrics.rect.top) },
            topRight: { x: Math.round(metrics.rect.right), y: Math.round(metrics.rect.top) },
            bottomLeft: { x: Math.round(metrics.rect.left), y: Math.round(metrics.rect.bottom) },
            bottomRight: { x: Math.round(metrics.rect.right), y: Math.round(metrics.rect.bottom) },
            center: { x: Math.round(metrics.centerX), y: Math.round(metrics.centerY) },
            width: Math.round(metrics.width),
            height: Math.round(metrics.height)
        };

        nodeData.pageCoordinates = {
            topLeft: { x: Math.round(metrics.absLeft), y: Math.round(metrics.absTop) },
            topRight: { x: Math.round(metrics.absLeft + metrics.width), y: Math.round(metrics.absTop) },
            bottomLeft: { x: Math.round(metrics.absLeft), y: Math.round(metrics.absTop + metrics.height) },
            bottomRight: { x: Math.round(metrics.absLeft + metrics.width), y: Math.round(metrics.absTop + metrics.height) },
            center: { x: Math.round(metrics.centerX + scrollX), y: Math.round(metrics.centerY + scrollY) },
            width: Math.round(metrics.width),
            height: Math.round(metrics.height)
        };

        // Add viewport and scroll information
        nodeData.viewport = {
            scrollX: Math.round(scrollX),
            scrollY: Math.round(scrollY),
            width: window.innerWidth,
            height: window.innerHeight
        };

        // Copy all attributes efficiently
        if (node.attributes) {
            const attributeNames = node.getAttributeNames?.() || [];
            for (const name of attributeNames) {
                nodeData.attributes[name] = node.getAttribute(name);
            }
        }

        // Highlight if criteria met
        if (isInteractive && isVisible && isTop) {
            nodeData.highlightIndex = highlightIndex++;
            if (doHighlightElements) {
                if (focusHighlightIndex >= 0) {
                    if (focusHighlightIndex === nodeData.highlightIndex) {
                        highlightElement(node, nodeData.highlightIndex, parentIframe);
                    }
                } else {
                    highlightElement(node, nodeData.highlightIndex, parentIframe);
                }
            }
        }

        // Handle special content for LI elements
        if (node.tagName === 'LI') {
            const caption = node.getAttribute('data-icon-caption');
            if (caption) {
                nodeData.text = caption.trim();
                nodeData.textContent = caption.trim();
            }
        }

        // Add parent context for specific elements
        if (node.classList.contains('icon-streamlined-add') && 
            node.classList.contains('clickable')) {
            const parentContext = [];
            let current = node;
            
            while (current && current.parentElement) {
                const viewClasses = current.parentElement.getAttribute('data-view-classes');
                if (viewClasses) {
                    parentContext.unshift(viewClasses);
                }
                current = current.parentElement;
            }
            
            if (parentContext.length > 0) {
                nodeData.attributes['parent_context'] = parentContext.join(' > ');
            }
        }

        if (node.classList.contains('search-input')) {
            const parentContext = [];
            let current = node;
            while (current && current.parentElement) {
                if (current.parentElement.classList.length > 0) {
                    parentContext.unshift(current.parentElement.classList.value);
                }
                current = current.parentElement;
            }
            
            if (parentContext.length > 0) {
                nodeData.attributes['parent_context'] = parentContext.join(' > ');
            }
        }

        // Only add shadowRoot field if it exists
        if (node.shadowRoot) {
            nodeData.shadowRoot = true;
        }

        // Handle shadow DOM - process all shadow children
        if (node.shadowRoot) {
            for (const shadowChild of node.shadowRoot.childNodes) {
                const shadowNode = buildDomTreeOptimized(shadowChild, parentIframe);
                if (shadowNode) {
                    nodeData.children.push(shadowNode);
                }
            }
        }

        // Handle frames and framesets - complete processing
        if (node.tagName === 'IFRAME' || node.tagName === 'FRAME' || node.tagName === 'FRAMESET') {
            try {
                let frameDoc;
                if (node.tagName === 'FRAMESET') {
                    const frameElements = Array.from(node.getElementsByTagName('frame'));
                    nodeData.frameContent = {
                        type: 'frameset',
                        accessible: true,
                        frames: frameElements.length
                    };
                    
                    for (const frameElement of frameElements) {
                        try {
                            const childFrameDoc = frameElement.contentDocument || frameElement.contentWindow?.document;
                            if (childFrameDoc?.body) {
                                const frameNodeData = {
                                    tagName: frameElement.tagName.toLowerCase(),
                                    attributes: {},
                                    children: []
                                };

                                const isFrameInteractive = isInteractiveFast(frameElement);
                                const isFrameVisible = isElementVisibleFast(frameElement);
                                const isFrameTop = isTopElementSimplified(frameElement);

                                if (isFrameInteractive && isFrameVisible && isFrameTop) {
                                    frameNodeData.highlightIndex = highlightIndex++;
                                    if (doHighlightElements) {
                                        if (focusHighlightIndex >= 0) {
                                            if (focusHighlightIndex === frameNodeData.highlightIndex) {
                                                highlightElement(frameElement, frameNodeData.highlightIndex, parentIframe);
                                            }
                                        } else {
                                            highlightElement(frameElement, frameNodeData.highlightIndex, parentIframe);
                                        }
                                    }
                                }

                                for (const child of childFrameDoc.body.childNodes) {
                                    const frameChildNode = buildDomTreeOptimized(child, frameElement);
                                    if (frameChildNode) {
                                        frameNodeData.children.push(frameChildNode);
                                    }
                                }
                                
                                nodeData.children.push(frameNodeData);
                            }
                        } catch (frameErr) {
                            console.warn(`Unable to access frame in frameset:`, frameErr);
                        }
                    }
                } else {
                    frameDoc = node.contentDocument || node.contentWindow?.document;
                    if (frameDoc?.body) {
                        nodeData.frameInfo = {
                            type: node.tagName.toLowerCase(),
                            id: node.id,
                            name: node.name,
                            src: node.getAttribute('src')
                        };
                        
                        nodeData.isFrameBoundary = true;
                        for (const child of frameDoc.body.childNodes) {
                            const frameChildNode = buildDomTreeOptimized(child, node);
                            if (frameChildNode) {
                                nodeData.children.push(frameChildNode);
                            }
                        }
                    }
                }
            } catch (e) {
                nodeData.frameContent = {
                    type: node.tagName.toLowerCase(),
                    src: node.getAttribute('src'),
                    accessible: false,
                    error: 'Cross-origin access denied'
                };
            }
        } else {
            // Process all regular children - no limits
            for (const child of node.childNodes) {
                const childNode = buildDomTreeOptimized(child, parentIframe);
                if (childNode) {
                    nodeData.children.push(childNode);
                }
            }
        }

        return nodeData;
    }

    return buildDomTreeOptimized(document.body);
} 