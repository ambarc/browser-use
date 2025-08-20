import asyncio
import json
import logging
import time
from typing import Callable, Dict, Optional, Type

from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel

from browser_use.agent.views import ActionModel, ActionResult
from browser_use.browser.context import BrowserContext
from browser_use.controller.registry.service import Registry
from browser_use.controller.views import (
	ClickElementAction,
	DoneAction,
	FailAction,
	GoToUrlAction,
	InputTextAction,
	NoParamsAction,
	OpenTabAction,
	ScrollAction,
	SearchGoogleAction,
	SendKeysAction,
	SwitchTabAction,
	WaitAction,
)
from browser_use.utils import time_execution_async, time_execution_sync

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)

from langchain_core.language_models.chat_models import BaseChatModel


class Controller:
	def __init__(
		self,
		exclude_actions: list[str] = [],
		output_model: Optional[Type[BaseModel]] = None,
		enable_recursive_frame_search: bool = True,  # Feature flag for recursive frame traversal
	):
		self.registry = Registry(exclude_actions)
		self.exclude_actions = exclude_actions
		self.output_model = output_model
		self.enable_recursive_frame_search = enable_recursive_frame_search
		
		self._register_default_actions()

	def _register_default_actions(self):
		"""Register all default browser actions"""

		if self.output_model is not None:

			@self.registry.action('Complete task', param_model=self.output_model)
			async def done(params: BaseModel):
				return ActionResult(is_done=True, extracted_content=params.model_dump_json())
		else:

			@self.registry.action('Complete task', param_model=DoneAction)
			async def done(params: DoneAction):
				return ActionResult(is_done=True, extracted_content=params.text)

		@self.registry.action('Fail task with custom message', param_model=FailAction)
		async def fail(params: FailAction):
			return ActionResult(is_done=True, error=params.message)

		@self.registry.action('Wait for specified duration', param_model=WaitAction)
		async def wait(params: WaitAction, browser: BrowserContext):
			import asyncio
			
			# Set default duration if not specified
			duration = params.duration_seconds if params.duration_seconds is not None else 5
			
			# Enforce minimum and maximum limits
			duration = max(0.5, min(duration, 60))  # Between 0.5 and 60 seconds
			
			reason = params.reason if params.reason else f"waiting for {duration} seconds"
			
			await asyncio.sleep(duration)
			
			msg = f'⏰ Waited for {duration} seconds: {reason}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		# Basic Navigation Actions
		@self.registry.action(
			'Search Google in the current tab',
			param_model=SearchGoogleAction,
		)
		async def search_google(params: SearchGoogleAction, browser: BrowserContext):
			page = await browser.get_current_page()
			await page.goto(f'https://www.google.com/search?q={params.query}&udm=14')
			await page.wait_for_load_state()
			msg = f'🔍  Searched for "{params.query}" in Google'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action('Navigate to URL in the current tab', param_model=GoToUrlAction)
		async def go_to_url(params: GoToUrlAction, browser: BrowserContext):
			page = await browser.get_current_page()
			await page.goto(params.url)
			await page.wait_for_load_state()
			msg = f'🔗  Navigated to {params.url}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action('Go back', param_model=NoParamsAction)
		async def go_back(_: NoParamsAction, browser: BrowserContext):
			await browser.go_back()
			msg = '🔙  Navigated back'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		# Element Interaction Actions
		# Track recent clicks to detect double-clicking behavior
		if not hasattr(self, '_recent_clicks'):
			self._recent_clicks = {}
		
		@self.registry.action('Click element', param_model=ClickElementAction)
		async def click_element(params: ClickElementAction, browser: BrowserContext):
			import time
			click_start_time = time.time()
			
			logger.info(json.dumps({
				"event": "click_element_start",
				"element_index": params.index,
				"timestamp": click_start_time
			}))
			
			session_start = time.time()
			session = await browser.get_session()
			state = session.cached_state
			session_duration = time.time() - session_start

			logger.info(json.dumps({
				"event": "click_element_session_acquired",
				"element_index": params.index,
				"duration_ms": round(session_duration * 1000, 1)
			}))

			if params.index not in state.selector_map:
				total_duration = time.time() - click_start_time
				logger.error(json.dumps({
					"event": "click_element_error",
					"element_index": params.index,
					"error_type": "element_not_found",
					"total_duration_ms": round(total_duration * 1000, 1)
				}))
				raise Exception(f'Element with index {params.index} does not exist - retry or use alternative actions')

			element_node = state.selector_map[params.index]
			initial_pages = len(session.context.pages)

			# Track click attempts for debugging double-clicking behavior
			click_attempt_id = f"click_{params.index}_{hash(element_node.xpath)}"
			current_time = time.time()
			
			# Check if this element was clicked recently (within 5 seconds)
			element_key = f"{params.index}_{element_node.xpath}"
			if element_key in self._recent_clicks:
				time_since_last = current_time - self._recent_clicks[element_key]
				if time_since_last < 5.0:  # 5 seconds threshold
					logger.warning(json.dumps({
						"event": "click_element_double_click_detected",
						"element_index": params.index,
						"click_attempt_id": click_attempt_id,
						"time_since_last_click_seconds": round(time_since_last, 2),
						"element_tag": element_node.tag_name,
						"element_xpath": element_node.xpath
					}))
			
			# Record this click
			self._recent_clicks[element_key] = current_time
			
			# Clean up old entries (older than 10 seconds)
			cutoff_time = current_time - 10.0
			self._recent_clicks = {k: v for k, v in self._recent_clicks.items() if v > cutoff_time}
			
			logger.info(json.dumps({
				"event": "click_element_attempt_start",
				"element_index": params.index,
				"click_attempt_id": click_attempt_id,
				"element_tag": element_node.tag_name,
				"element_xpath": element_node.xpath,
				"recent_clicks_count": len(self._recent_clicks)
			}))

			# if element has file uploader then dont click
			file_check_start = time.time()
			if await browser.is_file_uploader(element_node):
				file_check_duration = time.time() - file_check_start
				total_duration = time.time() - click_start_time
				msg = f'Index {params.index} - has an element which opens file upload dialog. To upload files please use a specific function to upload files '
				
				logger.info(json.dumps({
					"event": "click_element_file_uploader_detected",
					"element_index": params.index,
					"file_check_duration_ms": round(file_check_duration * 1000, 1),
					"total_duration_ms": round(total_duration * 1000, 1),
					"message": msg
				}))
				return ActionResult(extracted_content=msg, include_in_memory=True)
			
			file_check_duration = time.time() - file_check_start
			logger.info(json.dumps({
				"event": "click_element_file_check_complete",
				"element_index": params.index,
				"duration_ms": round(file_check_duration * 1000, 1),
				"is_file_uploader": False
			}))

			msg = None

			try:
				# Pass the click_attempt_id to track attempts
				click_execution_start = time.time()
				download_path = await browser._click_element_node(element_node, click_attempt_id=click_attempt_id)
				click_execution_duration = time.time() - click_execution_start
				
				if download_path:
					msg = f'💾  Downloaded file to {download_path}'
					logger.info(json.dumps({
						"event": "click_element_download_triggered",
						"element_index": params.index,
						"click_attempt_id": click_attempt_id,
						"download_path": download_path,
						"click_duration_ms": round(click_execution_duration * 1000, 1)
					}))
				else:
					element_text = element_node.get_all_text_till_next_clickable_element(max_depth=2)
					msg = f'🖱️  Clicked button with index {params.index}: {element_text}'
					logger.info(json.dumps({
						"event": "click_element_standard_click",
						"element_index": params.index,
						"click_attempt_id": click_attempt_id,
						"element_text": element_text,
						"click_duration_ms": round(click_execution_duration * 1000, 1)
					}))

				# Check for new tab
				if len(session.context.pages) > initial_pages:
					new_tab_msg = 'New tab opened - switching to it'
					msg += f' - {new_tab_msg}'
					
					tab_switch_start = time.time()
					await browser.switch_to_tab(-1)
					tab_switch_duration = time.time() - tab_switch_start
					
					logger.info(json.dumps({
						"event": "click_element_new_tab_opened",
						"element_index": params.index,
						"click_attempt_id": click_attempt_id,
						"new_tabs_count": len(session.context.pages) - initial_pages,
						"tab_switch_duration_ms": round(tab_switch_duration * 1000, 1)
					}))
				
				total_duration = time.time() - click_start_time
				logger.info(json.dumps({
					"event": "click_element_success",
					"element_index": params.index,
					"click_attempt_id": click_attempt_id,
					"total_duration_ms": round(total_duration * 1000, 1),
					"timing_breakdown": {
						"session_ms": round(session_duration * 1000, 1),
						"file_check_ms": round(file_check_duration * 1000, 1),
						"click_execution_ms": round(click_execution_duration * 1000, 1)
					}
				}))
				return ActionResult(extracted_content=msg, include_in_memory=True)
			except Exception as e:
				total_duration = time.time() - click_start_time
				logger.error(json.dumps({
					"event": "click_element_failed",
					"element_index": params.index,
					"click_attempt_id": click_attempt_id,
					"error": str(e),
					"total_duration_ms": round(total_duration * 1000, 1)
				}))
				logger.warning(json.dumps({
					"event": "click_element_not_clickable",
					"element_index": params.index,
					"message": "Element not clickable - most likely the page changed"
				}))
				return ActionResult(error=str(e))

		@self.registry.action(
			'Input text into a input interactive element',
			param_model=InputTextAction,
		)
		async def input_text(params: InputTextAction, browser: BrowserContext):
			session = await browser.get_session()
			state = session.cached_state

			if params.index not in state.selector_map:
				raise Exception(f'Element index {params.index} does not exist - retry or use alternative actions')

			element_node = state.selector_map[params.index]
			await browser._input_text_element_node(element_node, params.text)
			msg = f'⌨️  Input {params.text} into index {params.index}'
			logger.info(msg)
			#logger.debug(f'Element xpath: {element_node.xpath}')
			return ActionResult(extracted_content=msg, include_in_memory=True)

		# Tab Management Actions
		@self.registry.action('Switch tab', param_model=SwitchTabAction)
		async def switch_tab(params: SwitchTabAction, browser: BrowserContext):
			await browser.switch_to_tab(params.page_id)
			# Wait for tab to be ready
			page = await browser.get_current_page()
			await page.wait_for_load_state()
			msg = f'🔄  Switched to tab {params.page_id}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action('Open url in new tab', param_model=OpenTabAction)
		async def open_tab(params: OpenTabAction, browser: BrowserContext):
			await browser.create_new_tab(params.url)
			msg = f'🔗  Opened new tab with {params.url}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		# Content Actions
		@self.registry.action(
			'Extract page content to retrieve specific information from the page, e.g. all company names, a specifc description, all information about, links with companies in structured format or simply links',
		)
		async def extract_content(goal: str, browser: BrowserContext, page_extraction_llm: BaseChatModel):
			page = await browser.get_current_page()
			import markdownify

			content = markdownify.markdownify(await page.content())
			# ambar - LLama believes it has to extract; that's OK -- that's promptable. but why are its evals stuck?
			prompt = 'Your task is to extract the content of the page. You will be given a page and a goal and you should extract all relevant information around this goal from the page. If the goal is vague, summarize the page. Respond in json format. DO NOT include any non-JSON text in your response, including introductions to the JSON, or extraneous markdown formatting. Extraction goal: {goal}, Page: {page}'
			# ambar - this uses prompt template from langchain. if we're using llama, how does that affect
			template = PromptTemplate(input_variables=['goal', 'page'], template=prompt)
			try:
				output = page_extraction_llm.invoke(template.format(goal=goal, page=content))
				msg = f'📄  Extracted from page\n: {output.content}\n'
				return ActionResult(extracted_content=msg, include_in_memory=True)
			except Exception as e:
				# ambar - is this a false error? red herring?
				logger.error(f'Error extracting content: {e}')
				# logger.debug(f'Error extracting content: {e}, {output.content}')
				msg = f'📄  Extracted from page\n: {content}\n'
				# logger.info(msg)
				return ActionResult(extracted_content=msg)

		# @self.registry.action(
		# 	'Scroll down the page or a specific frame by pixel amount - if no amount is specified, scroll down one page',
		# 	param_model=ScrollAction,
		# )
		async def scroll_down(params: ScrollAction, browser: BrowserContext):
			page = await browser.get_current_page()
			
			# More robust script with proper error handling, waiting, and fallbacks
			script = f"""
			async (args) => {{
				// Helper to safely get computed heights
				function safeGetHeight(win) {{
					try {{
						return win.document.body.scrollHeight || 
							   win.document.documentElement.scrollHeight || 
							   win.innerHeight;
					}} catch (e) {{
						return win.innerHeight || 600; // Fallback height
					}}
				}}
				
				{"" if not self.enable_recursive_frame_search else '''
				// Recursive function to find frame in all documents
				function findFrameRecursive(doc, frameId) {
					// Try direct ID lookup
					try {
						const frame = doc.getElementById(frameId);
						if (frame) return frame;
					} catch (e) {}
					
					// Try name lookup
					try {
						const frame = Array.from(doc.querySelectorAll('iframe, frame'))
							.find(f => f.name === frameId);
						if (frame) return frame;
					} catch (e) {}
					
					// Try CSS selector
					try {
						const frame = doc.querySelector(frameId);
						if (frame) return frame;
					} catch (e) {}
					
					// Recursively search in all frames
					try {
						const frames = doc.querySelectorAll('iframe, frame');
						for (const frame of frames) {
							try {
								if (frame.contentDocument) {
									const found = findFrameRecursive(frame.contentDocument, frameId);
									if (found) return found;
								}
							} catch (e) {
								// Cross-origin frame, skip
							}
						}
					} catch (e) {}
					
					return null;
				}
				'''}
				
				// Wait for frames to be accessible
				await new Promise(r => setTimeout(r, 300));
				
				if (args.frameId) {{
					{"// Find the frame using recursive search" if self.enable_recursive_frame_search else "// Find the frame (with multiple strategies)"}
					{"let frame = findFrameRecursive(document, args.frameId);" if self.enable_recursive_frame_search else '''let frame = null;
					
					// Method 1: Direct ID
					try { frame = document.getElementById(args.frameId); } catch (e) {}
					
					// Method 2: Name
					if (!frame) {
						try {
							frame = Array.from(document.querySelectorAll('iframe, frame'))
								.find(f => f.name === args.frameId);
						} catch (e) {}
					}
					
					// Method 3: Try a CSS selector
					if (!frame) {
						try {
							frame = document.querySelector(args.frameId);
						} catch (e) {}
					}'''}
					
					// If frame found, scroll it with retries
					if (frame) {{
						let scrolled = false;
						let retries = 0;
						
						while (!scrolled && retries < 3) {{
							try {{
								// Wait for frame to be fully loaded
								await new Promise(r => setTimeout(r, 100 * (retries + 1)));
								
								if (!frame.contentWindow) {{
									return {{ 
										success: false, 
										error: 'Frame has no content window - might be cross-origin restricted' 
									}};
								}}
								
								// Get scroll amount (default: 80% of frame height for more reliable scrolling)
								const frameWin = frame.contentWindow;
								const amount = args.amount !== null ? 
									args.amount : 
									Math.floor(frameWin.innerHeight * 0.8);
								
								// Get current position and frame dimensions
								const beforeScroll = frameWin.scrollY || 0;
								const frameHeight = frameWin.innerHeight;
								const contentHeight = safeGetHeight(frameWin);
								const maxScroll = Math.max(0, contentHeight - frameHeight);
								
								// Check if frame window is scrollable
								if (maxScroll <= 0) {{
									// Frame window isn't scrollable, try to find scrollable elements inside
									const scrollableElements = [];
									const allElements = frameWin.document.querySelectorAll('*');
									
									for (const elem of allElements) {{
										try {{
											const computedStyle = frameWin.getComputedStyle(elem);
											const overflowY = computedStyle.overflowY;
											
											// Check if element has scrollable content
											const hasScrollableContent = elem.scrollHeight > elem.clientHeight;
											const hasScrollableStyle = overflowY === 'auto' || overflowY === 'scroll';
											
											if (hasScrollableContent && (hasScrollableStyle || elem.scrollHeight > elem.clientHeight + 10)) {{
												// Test if element can actually be scrolled
												const beforeTest = elem.scrollTop;
												elem.scrollTop += 1;
												const afterTest = elem.scrollTop;
												elem.scrollTop = beforeTest; // Restore
												
												if (afterTest > beforeTest) {{
													scrollableElements.push({{
														element: elem,
														scrollHeight: elem.scrollHeight,
														clientHeight: elem.clientHeight,
														maxScroll: elem.scrollHeight - elem.clientHeight,
														tagName: elem.tagName,
														id: elem.id,
														className: elem.className
													}});
												}}
											}}
										}} catch (e) {{
											// Skip elements that can't be accessed
										}}
									}}
									
									// Try to scroll the best scrollable element
									if (scrollableElements.length > 0) {{
										// Sort by scroll potential (largest scrollable area first)
										scrollableElements.sort((a, b) => b.maxScroll - a.maxScroll);
										const bestElement = scrollableElements[0];
										
										const beforeScroll = bestElement.element.scrollTop;
										
										// Use more aggressive scrolling to ensure content is visible to humans
										// Account for viewport differences and ensure content is clearly in view
										const scrollAmount = Math.min(amount, bestElement.maxScroll * 0.9);
										const minScrollAmount = Math.min(bestElement.clientHeight * 0.7, scrollAmount); // Ensure we scroll at least 70% of viewport
										
										bestElement.element.scrollTop += Math.max(scrollAmount, minScrollAmount);
										
										await new Promise(r => setTimeout(r, 100));
										
										const afterScroll = bestElement.element.scrollTop;
										const actualScrolled = afterScroll - beforeScroll;
										const isAtBottom = afterScroll >= bestElement.maxScroll - 20; // Larger tolerance for "at bottom"
										
										if (actualScrolled > 0 || beforeScroll >= bestElement.maxScroll - 5) {{
											return {{
												success: true,
												context: 'frame',
												id: frame.id || frame.name || 'unnamed frame',
												scrollAmount: actualScrolled,
												scrollMax: isAtBottom,
												scrollTarget: `element ${{bestElement.tagName}}${{bestElement.id ? '#' + bestElement.id : ''}}${{bestElement.className ? '.' + bestElement.className.split(' ')[0] : ''}}`,
												debug: `scrolled element - before: ${{beforeScroll}}, after: ${{afterScroll}}, maxScroll: ${{bestElement.maxScroll}}, found ${{scrollableElements.length}} scrollable elements`
											}};
										}}
									}}
									
									return {{
										success: false,
										error: `Frame '${{frame.id || frame.name}}' window not scrollable (content: ${{contentHeight}}px, frame: ${{frameHeight}}px) and no scrollable elements found inside`
									}};
								}}
								
								// Execute scroll
								frameWin.scrollBy({{
									top: amount,
									behavior: 'auto'  // Use 'auto' instead of 'smooth' for reliability
								}});
								
								// Small delay to allow scroll to complete
								await new Promise(r => setTimeout(r, 100));
								
								// Verify scroll occurred
								const afterScroll = frameWin.scrollY || 0;
								const actualScrolled = afterScroll - beforeScroll;
								const isAtBottom = afterScroll >= maxScroll - 5; // 5px tolerance
								
								// Only consider successful if we actually scrolled OR were already at bottom
								scrolled = (actualScrolled > 0) || (beforeScroll >= maxScroll - 5);
								
								if (scrolled) {{
									return {{ 
										success: true, 
										context: 'frame', 
										id: frame.id || frame.name || 'unnamed frame',
										scrollAmount: actualScrolled,
										scrollMax: isAtBottom,
										debug: `before: ${{beforeScroll}}, after: ${{afterScroll}}, content: ${{contentHeight}}, frame: ${{frameHeight}}, maxScroll: ${{maxScroll}}`
									}};
								}} else {{
									return {{
										success: false,
										error: `Scroll failed - before: ${{beforeScroll}}, after: ${{afterScroll}}, tried to scroll: ${{amount}}px, content: ${{contentHeight}}, frame: ${{frameHeight}}`
									}};
								}}
							}} catch (e) {{
								retries++;
								if (retries >= 3) {{
									return {{ 
										success: false, 
										error: `Frame access error: ${{e.toString()}}. This may be due to cross-origin restrictions.` 
									}};
								}}
							}}
						}}
						
						return {{ 
							success: false, 
							error: 'Failed to scroll frame after multiple attempts' 
						}};
					}} else {{
						return {{ 
							success: false, 
							error: `Frame not found: ${{args.frameId}}` 
						}};
					}}
				}} else {{
					// Scroll main window with verification
					try {{
						const beforeScroll = window.scrollY;
						const amount = args.amount !== null ? args.amount : window.innerHeight;
						
						window.scrollBy({{
							top: amount,
							behavior: 'auto'
						}});
						
						// Small delay to allow scroll to complete
						await new Promise(r => setTimeout(r, 100));
						
						// Verify scroll occurred
						const afterScroll = window.scrollY;
						const scrolled = (afterScroll > beforeScroll) || 
									   (beforeScroll >= document.documentElement.scrollHeight - window.innerHeight);
									   
						return {{ 
							success: scrolled, 
							context: 'main',
							scrollAmount: afterScroll - beforeScroll,
							scrollMax: afterScroll >= document.documentElement.scrollHeight - window.innerHeight
						}};
					}} catch (e) {{
						return {{ success: false, error: e.toString() }};
					}}
				}}
			}}
			"""
			
			try:
				result = await page.evaluate(script, {
					'frameId': params.frame_id,
					'amount': params.amount
				})
				
				if result.get('success'):
					context = result.get('context', 'page')
					target = f" in {context}" if context == 'frame' else ""
					frame_id = f" '{result.get('id')}'" if context == 'frame' else ""
					amt_text = "" if params.amount is None else f" {params.amount}px"
					
					# Add scroll position details
					position_text = ""
					if result.get('scrollMax'):
						position_text = " (reached bottom)"
					elif result.get('scrollAmount') is not None:
						position_text = f" ({result.get('scrollAmount')}px)"
					
					# Add scroll target info
					scroll_target = ""
					if result.get('scrollTarget'):
						scroll_target = f" -> {result.get('scrollTarget')}"
					
					# Add debug info for frames
					debug_info = ""
					if result.get('debug') and context == 'frame':
						debug_info = f" [DEBUG: {result.get('debug')}]"
						
					msg = f'⬇️  Scrolled down{amt_text}{target}{frame_id}{scroll_target}{position_text}{debug_info}'
				else:
					error = result.get('error', 'unknown error')
					msg = f'❌ Failed to scroll: {error}'
					
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)
			except Exception as e:
				logger.error(f"Scroll operation failed: {str(e)}")
				return ActionResult(
					error=f"Scroll operation failed: {str(e)}",
					include_in_memory=True
				)

		# scroll up
		# @self.registry.action(
		# 	'Scroll up the page or a specific frame by pixel amount - if no amount is specified, scroll up one page',
		# 	param_model=ScrollAction,
		# )
		async def scroll_up(params: ScrollAction, browser: BrowserContext):
			page = await browser.get_current_page()
			
			# Script with optional recursive frame traversal
			script = f"""
			async (args) => {{
				// Helper to safely get computed heights
				function safeGetHeight(win) {{
					try {{
						return win.document.body.scrollHeight || 
							   win.document.documentElement.scrollHeight || 
							   win.innerHeight;
					}} catch (e) {{
						return win.innerHeight || 600; // Fallback height
					}}
				}}
				
				{"" if not self.enable_recursive_frame_search else '''
				// Recursive function to find frame in all documents
				function findFrameRecursive(doc, frameId) {
					// Try direct ID lookup
					try {
						const frame = doc.getElementById(frameId);
						if (frame) return frame;
					} catch (e) {}
					
					// Try name lookup
					try {
						const frame = Array.from(doc.querySelectorAll('iframe, frame'))
							.find(f => f.name === frameId);
						if (frame) return frame;
					} catch (e) {}
					
					// Try CSS selector
					try {
						const frame = doc.querySelector(frameId);
						if (frame) return frame;
					} catch (e) {}
					
					// Recursively search in all frames
					try {
						const frames = doc.querySelectorAll('iframe, frame');
						for (const frame of frames) {
							try {
								if (frame.contentDocument) {
									const found = findFrameRecursive(frame.contentDocument, frameId);
									if (found) return found;
								}
							} catch (e) {
								// Cross-origin frame, skip
							}
						}
					} catch (e) {}
					
					return null;
				}
				'''}
				
				// Wait for frames to be accessible
				await new Promise(r => setTimeout(r, 300));
				
				if (args.frameId) {{
					{"// Find the frame using recursive search" if self.enable_recursive_frame_search else "// Find the frame (try ID first, then name)"}
					{"let frame = findFrameRecursive(document, args.frameId);" if self.enable_recursive_frame_search else '''let frame = document.getElementById(args.frameId);
					if (!frame) {
						frame = Array.from(document.querySelectorAll('iframe, frame'))
							.find(f => f.name === args.frameId);
					}'''}
					
					// If frame found, scroll it
					if (frame && frame.contentWindow) {{
						try {{
							const frameWin = frame.contentWindow;
							const amount = args.amount !== null ? args.amount : frameWin.innerHeight;
							
							// Get current position and frame dimensions
							const beforeScroll = frameWin.scrollY || 0;
							const frameHeight = frameWin.innerHeight;
							const contentHeight = safeGetHeight(frameWin);
							const maxScroll = Math.max(0, contentHeight - frameHeight);
							
							// Check if frame window is scrollable
							if (maxScroll <= 0) {{
								// Frame window isn't scrollable, try to find scrollable elements inside
								const scrollableElements = [];
								const allElements = frameWin.document.querySelectorAll('*');
								
								for (const elem of allElements) {{
									try {{
										const computedStyle = frameWin.getComputedStyle(elem);
										const overflowY = computedStyle.overflowY;
										
										// Check if element has scrollable content
										const hasScrollableContent = elem.scrollHeight > elem.clientHeight;
										const hasScrollableStyle = overflowY === 'auto' || overflowY === 'scroll';
										
										if (hasScrollableContent && (hasScrollableStyle || elem.scrollHeight > elem.clientHeight + 10)) {{
											// Test if element can actually be scrolled
											const beforeTest = elem.scrollTop;
											elem.scrollTop += 1;
											const afterTest = elem.scrollTop;
											elem.scrollTop = beforeTest; // Restore
											
											if (afterTest > beforeTest) {{
												scrollableElements.push({{
													element: elem,
													scrollHeight: elem.scrollHeight,
													clientHeight: elem.clientHeight,
													maxScroll: elem.scrollHeight - elem.clientHeight,
													tagName: elem.tagName,
													id: elem.id,
													className: elem.className
												}});
											}}
										}}
									}} catch (e) {{
										// Skip elements that can't be accessed
									}}
								}}
								
								// Try to scroll the best scrollable element
								if (scrollableElements.length > 0) {{
									// Sort by scroll potential (largest scrollable area first)
									scrollableElements.sort((a, b) => b.maxScroll - a.maxScroll);
									const bestElement = scrollableElements[0];
									
									const beforeScroll = bestElement.element.scrollTop;
									bestElement.element.scrollTop -= Math.min(amount, bestElement.maxScroll * 0.8);
									
									await new Promise(r => setTimeout(r, 100));
									
									const afterScroll = bestElement.element.scrollTop;
									const actualScrolled = beforeScroll - afterScroll;  // Positive when scrolling up
									const isAtTop = afterScroll <= 5;
									
									if (actualScrolled > 0 || beforeScroll <= 5) {{
										return {{
											success: true,
											context: 'frame',
											id: frame.id || frame.name || 'unnamed frame',
											scrollAmount: actualScrolled,
											scrollMax: isAtTop,
											scrollTarget: `element ${{bestElement.tagName}}${{bestElement.id ? '#' + bestElement.id : ''}}${{bestElement.className ? '.' + bestElement.className.split(' ')[0] : ''}}`,
											debug: `scrolled element - before: ${{beforeScroll}}, after: ${{afterScroll}}, maxScroll: ${{bestElement.maxScroll}}, found ${{scrollableElements.length}} scrollable elements`
										}};
									}}
								}}
								
								return {{
									success: false,
									error: `Frame '${{frame.id || frame.name}}' window not scrollable (content: ${{contentHeight}}px, frame: ${{frameHeight}}px) and no scrollable elements found inside`
								}};
							}}
							
							// Execute scroll
							frameWin.scrollBy(0, -amount);  // Negative for upward scroll
							
							// Small delay to allow scroll to complete
							await new Promise(r => setTimeout(r, 100));
							
							// Verify scroll occurred
							const afterScroll = frameWin.scrollY || 0;
							const actualScrolled = beforeScroll - afterScroll;  // Positive when scrolling up
							const isAtTop = afterScroll <= 5; // 5px tolerance
							
							// Only consider successful if we actually scrolled OR were already at top
							const scrolled = (actualScrolled > 0) || (beforeScroll <= 5);
							
							if (scrolled) {{
								return {{ 
									success: true, 
									context: 'frame', 
									id: frame.id || frame.name || 'unnamed frame',
									scrollAmount: actualScrolled,
									scrollMax: isAtTop,
									debug: `before: ${{beforeScroll}}, after: ${{afterScroll}}, content: ${{contentHeight}}, frame: ${{frameHeight}}, maxScroll: ${{maxScroll}}`
								}};
							}} else {{
								return {{
									success: false,
									error: `Scroll failed - before: ${{beforeScroll}}, after: ${{afterScroll}}, tried to scroll: ${{amount}}px up, content: ${{contentHeight}}, frame: ${{frameHeight}}`
								}};
							}}
						}} catch (e) {{
							return {{ success: false, error: e.toString() }};
						}}
					}} else {{
						return {{ success: false, error: 'Frame not found' }};
					}}
				}} else {{
					// Scroll main window
					const amount = args.amount !== null ? args.amount : window.innerHeight;
					window.scrollBy(0, -amount);  // Negative for upward scroll
					return {{ success: true, context: 'main' }};
				}}
			}}
			"""
			
			result = await page.evaluate(script, {
				'frameId': params.frame_id,
				'amount': params.amount
			})
			
			if result.get('success'):
				context = result.get('context', 'page')
				target = f" in {context}" if context == 'frame' else ""
				frame_id = f" '{result.get('id')}'" if context == 'frame' else ""
				amt_text = "" if params.amount is None else f" {params.amount}px"
				
				# Add scroll position details
				position_text = ""
				if result.get('scrollMax'):
					position_text = " (reached top)"
				elif result.get('scrollAmount') is not None:
					position_text = f" ({result.get('scrollAmount')}px)"
				
				# Add scroll target info
				scroll_target = ""
				if result.get('scrollTarget'):
					scroll_target = f" -> {result.get('scrollTarget')}"
				
				# Add debug info for frames
				debug_info = ""
				if result.get('debug') and context == 'frame':
					debug_info = f" [DEBUG: {result.get('debug')}]"
					
				msg = f'⬆️  Scrolled up{amt_text}{target}{frame_id}{scroll_target}{position_text}{debug_info}'
			else:
				error = result.get('error', 'unknown error')
				msg = f'❌ Failed to scroll: {error}'
				logger.info(msg)
			return ActionResult(error=msg, include_in_memory=True)

		# Added by bitboard - Natural scrolling methods for testing different scroll behaviors
		@self.registry.action(
			'Scroll down using natural mouse wheel at page center - more human-like scrolling behavior',
			param_model=ScrollAction,
		)
		async def scroll_down_natural(params: ScrollAction, browser: BrowserContext):
			"""Natural scroll down using positioned mouse wheel at page center"""
			page = await browser.get_current_page()
			delta_y = params.amount if params.amount is not None else 400
			msg = ""
			try:
				# Get page dimensions for mouse positioning
				viewport = page.viewport_size
				if viewport is None:
					dimensions = await page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
					center_x = dimensions["width"] // 2
					center_y = dimensions["height"] // 2
				else:
					center_x = viewport["width"] // 2
					center_y = viewport["height"] // 2
				
				# Get initial scroll positions for both main page and any accessible iframes
				initial_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						// Check all iframes for their scroll positions
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									const rect = iframe.getBoundingClientRect();
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0,
										scrollHeight: frameDoc.documentElement.scrollHeight || frameDoc.body.scrollHeight,
										clientHeight: frameWindow.innerHeight || frameDoc.documentElement.clientHeight,
										rect: {
											x: rect.x,
											y: rect.y,
											width: rect.width,
											height: rect.height
										}
									});
								}
							} catch (e) {
								// Cross-origin iframe
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Move mouse to center of page and perform wheel scroll
				await page.mouse.move(center_x, center_y)
				await asyncio.sleep(0.1)  # Small delay to ensure mouse position is registered
				await page.mouse.wheel(0, delta_y)
				await asyncio.sleep(0.3)  # Wait for scroll to complete
				
				# Get final scroll positions
				final_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Calculate scroll differences
				main_scrolled = final_state['mainWindow'] - initial_state['mainWindow']
				iframe_scrolled = {}
				total_scrolled = main_scrolled
				
				for i, (initial_iframe, final_iframe) in enumerate(zip(initial_state['iframes'], final_state['iframes'])):
					if 'error' not in initial_iframe and 'error' not in final_iframe:
						scrolled = final_iframe['scrollY'] - initial_iframe['scrollY']
						iframe_scrolled[i] = scrolled
						if scrolled != 0:
							total_scrolled = scrolled  # Use iframe scroll as primary if it happened
				
				# Determine which element actually scrolled
				scrolled_target = "none"
				if main_scrolled != 0:
					scrolled_target = "main_window"
				for iframe_idx, scrolled in iframe_scrolled.items():
					if scrolled != 0:
						iframe_id = initial_state['iframes'][iframe_idx]['id']
						scrolled_target = f"iframe_{iframe_idx}_{iframe_id}"
						break
				
				msg = f'🎯 Natural scroll down: {total_scrolled}px (mouse wheel at {center_x},{center_y}) -> {scrolled_target}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)
				
			except Exception as e:
				msg = f'❌ Natural scroll down failed: {str(e)}'
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

		# Added by bitboard - Scroll to bottom using End key
		@self.registry.action(
			'Scroll to bottom of page or frame using End key - instant navigation to bottom',
			param_model=ScrollAction,
		)
		async def scroll_down_to_bottom(params: ScrollAction, browser: BrowserContext):
			"""Scroll to bottom using End key with proper focus handling"""
			page = await browser.get_current_page()
			
			try:
				# Get page dimensions for focus positioning
				viewport = page.viewport_size
				if viewport is None:
					dimensions = await page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
					center_x = dimensions["width"] // 2
					center_y = dimensions["height"] // 2
				else:
					center_x = viewport["width"] // 2
					center_y = viewport["height"] // 2
				
				# Get initial scroll positions
				initial_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0,
										scrollHeight: frameDoc.documentElement.scrollHeight || frameDoc.body.scrollHeight,
										clientHeight: frameWindow.innerHeight || frameDoc.documentElement.clientHeight
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Move mouse to center and click to focus, then press End key
				await page.mouse.move(center_x, center_y)
				await page.mouse.click(center_x, center_y)
				await asyncio.sleep(0.2)  # Wait for focus
				await page.keyboard.press('End')
				await asyncio.sleep(0.5)  # Wait for scroll to complete
				
				# Get final scroll positions
				final_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Calculate scroll differences and check if reached bottom
				main_scrolled = final_state['mainWindow'] - initial_state['mainWindow']
				iframe_scrolled = {}
				total_scrolled = main_scrolled
				reached_bottom = False
				
				for i, (initial_iframe, final_iframe) in enumerate(zip(initial_state['iframes'], final_state['iframes'])):
					if 'error' not in initial_iframe and 'error' not in final_iframe:
						scrolled = final_iframe['scrollY'] - initial_iframe['scrollY']
						iframe_scrolled[i] = scrolled
						if scrolled != 0:
							total_scrolled = scrolled
							# Check if iframe reached bottom
							max_scroll = initial_iframe['scrollHeight'] - initial_iframe['clientHeight']
							current_scroll = final_iframe['scrollY']
							reached_bottom = current_scroll >= max_scroll - 10  # 10px tolerance
				
				# Check if main window reached bottom
				if main_scrolled != 0:
					window_info = await page.evaluate("({scrollY: window.scrollY, maxScroll: document.documentElement.scrollHeight - window.innerHeight})")
					reached_bottom = window_info['scrollY'] >= window_info['maxScroll'] - 10
				
				bottom_text = " (reached bottom)" if reached_bottom else ""
				msg = f'⬇️ Scroll to bottom: {total_scrolled}px (End key){bottom_text}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)
				
			except Exception as e:
				msg = f'❌ Scroll to bottom failed: {str(e)}'
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

		# Added by bitboard - Natural scroll up using mouse wheel
		@self.registry.action(
			'Scroll up using natural mouse wheel at page center - more human-like scrolling behavior',
			param_model=ScrollAction,
		)
		async def scroll_up_natural(params: ScrollAction, browser: BrowserContext):
			"""Natural scroll up using positioned mouse wheel at page center"""
			page = await browser.get_current_page()
			delta_y = -(params.amount if params.amount is not None else 400)  # Negative for upward scroll
			
			try:
				# Get page dimensions for mouse positioning
				viewport = page.viewport_size
				if viewport is None:
					dimensions = await page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
					center_x = dimensions["width"] // 2
					center_y = dimensions["height"] // 2
				else:
					center_x = viewport["width"] // 2
					center_y = viewport["height"] // 2
				
				# Get initial scroll positions
				initial_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Move mouse to center and perform wheel scroll
				await page.mouse.move(center_x, center_y)
				await asyncio.sleep(0.1)
				await page.mouse.wheel(0, delta_y)  # Negative delta for upward scroll
				await asyncio.sleep(0.3)
				
				# Get final scroll positions
				final_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Calculate scroll differences (positive when scrolling up)
				main_scrolled = initial_state['mainWindow'] - final_state['mainWindow']
				iframe_scrolled = {}
				total_scrolled = main_scrolled
				
				for i, (initial_iframe, final_iframe) in enumerate(zip(initial_state['iframes'], final_state['iframes'])):
					if 'error' not in initial_iframe and 'error' not in final_iframe:
						scrolled = initial_iframe['scrollY'] - final_iframe['scrollY']  # Positive when scrolling up
						iframe_scrolled[i] = scrolled
						if scrolled != 0:
							total_scrolled = scrolled
				
				# Determine which element actually scrolled
				scrolled_target = "none"
				if main_scrolled != 0:
					scrolled_target = "main_window"
				for iframe_idx, scrolled in iframe_scrolled.items():
					if scrolled != 0:
						iframe_id = initial_state['iframes'][iframe_idx]['id']
						scrolled_target = f"iframe_{iframe_idx}_{iframe_id}"
						break
				
				msg = f'🎯 Natural scroll up: {total_scrolled}px (mouse wheel at {center_x},{center_y}) -> {scrolled_target}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)
				
			except Exception as e:
				msg = f'❌ Natural scroll up failed: {str(e)}'
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

		# Added by bitboard - Scroll to top using Home key
		@self.registry.action(
			'Scroll to top of page or frame using Home key - instant navigation to top',
			param_model=ScrollAction,
		)
		async def scroll_up_to_top(params: ScrollAction, browser: BrowserContext):
			"""Scroll to top using Home key with proper focus handling"""
			page = await browser.get_current_page()
			
			try:
				# Get page dimensions for focus positioning
				viewport = page.viewport_size
				if viewport is None:
					dimensions = await page.evaluate("({width: window.innerWidth, height: window.innerHeight})")
					center_x = dimensions["width"] // 2
					center_y = dimensions["height"] // 2
				else:
					center_x = viewport["width"] // 2
					center_y = viewport["height"] // 2
				
				# Get initial scroll positions
				initial_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Move mouse to center and click to focus, then press Home key
				await page.mouse.move(center_x, center_y)
				await page.mouse.click(center_x, center_y)
				await asyncio.sleep(0.2)
				await page.keyboard.press('Home')
				await asyncio.sleep(0.5)
				
				# Get final scroll positions
				final_state = await page.evaluate("""
					() => {
						const state = {
							mainWindow: window.scrollY,
							iframes: []
						};
						
						const iframes = document.querySelectorAll('iframe, frame');
						iframes.forEach((iframe, index) => {
							try {
								const frameWindow = iframe.contentWindow;
								const frameDoc = iframe.contentDocument;
								if (frameWindow && frameDoc) {
									state.iframes.push({
										index: index,
										id: iframe.id || '',
										scrollY: frameWindow.scrollY || frameDoc.documentElement.scrollTop || frameDoc.body.scrollTop || 0
									});
								}
							} catch (e) {
								state.iframes.push({
									index: index,
									id: iframe.id || '',
									error: 'Cross-origin access denied'
								});
							}
						});
						
						return state;
					}
				""")
				
				# Calculate scroll differences (positive when scrolling up)
				main_scrolled = initial_state['mainWindow'] - final_state['mainWindow']
				iframe_scrolled = {}
				total_scrolled = main_scrolled
				reached_top = False
				
				for i, (initial_iframe, final_iframe) in enumerate(zip(initial_state['iframes'], final_state['iframes'])):
					if 'error' not in initial_iframe and 'error' not in final_iframe:
						scrolled = initial_iframe['scrollY'] - final_iframe['scrollY']  # Positive when scrolling up
						iframe_scrolled[i] = scrolled
						if scrolled != 0:
							total_scrolled = scrolled
							# Check if iframe reached top
							reached_top = final_iframe['scrollY'] <= 5
				
				# Check if main window reached top
				if main_scrolled != 0:
					reached_top = final_state['mainWindow'] <= 5
				
				top_text = " (reached top)" if reached_top else ""
				msg = f'⬆️ Scroll to top: {total_scrolled}px (Home key){top_text}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)
				
			except Exception as e:
				msg = f'❌ Scroll to top failed: {str(e)}'
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

		# send keys
		@self.registry.action(
			'Send strings of special keys like Backspace, Insert, PageDown, Delete, Enter, Shortcuts such as `Control+o`, `Control+Shift+T` are supported as well. This gets used in keyboard.press. Be aware of different operating systems and their shortcuts',
			param_model=SendKeysAction,
		)
		async def send_keys(params: SendKeysAction, browser: BrowserContext):
			page = await browser.get_current_page()
			
			# ambar - wytf is llama adding this?
			# Handle special key formatting - remove curly braces if present
			key = params.keys
			if key.startswith('{') and key.endswith('}'):
				key = key[1:-1]
			
			await page.keyboard.press(key)
			msg = f'⌨️  Sent keys: {params.keys}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action(
			description='If you dont find something which you want to interact with, scroll to it',
		)
		async def scroll_to_text(text: str, browser: BrowserContext):  # type: ignore
			page = await browser.get_current_page()
			try:
				# Try different locator strategies
				locators = [
					page.get_by_text(text, exact=False),
					page.locator(f'text={text}'),
					page.locator(f"//*[contains(text(), '{text}')]"),
					# Add strategies for data-icon-caption
					page.locator(f"//*[@data-icon-caption='{text}']"),
					page.locator(f"//*[contains(@data-icon-caption, '{text}')]"),
					# Try case-insensitive match
					page.locator(f"//*[translate(@data-icon-caption, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='{text.lower()}']")
				]

				for locator in locators:
					try:
						# First check if element exists and is visible
						if await locator.count() > 0 and await locator.first.is_visible():
							await locator.first.scroll_into_view_if_needed()
							await asyncio.sleep(0.5)  # Wait for scroll to complete
							msg = f'🔍  Scrolled to text: {text}'
							logger.info(msg)
							return ActionResult(extracted_content=msg, include_in_memory=True)
					except Exception as e:
						logger.debug(f'Locator attempt failed: {str(e)}')
						continue

				msg = f"Text '{text}' not found or not visible on page"
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)

			except Exception as e:
				msg = f"Failed to scroll to text '{text}': {str(e)}"
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

		@self.registry.action(
			description='Get all options from a native dropdown',
		)
		async def get_dropdown_options(index: int, browser: BrowserContext) -> ActionResult:
			"""Get all options from a native dropdown"""
			page = await browser.get_current_page()
			selector_map = await browser.get_selector_map()
			dom_element = selector_map[index]

			try:
				# Frame-aware approach since we know it works
				all_options = []
				frame_index = 0

				for frame in page.frames:
					try:
						options = await frame.evaluate(
							"""
							(xpath) => {
								const select = document.evaluate(xpath, document, null,
									XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
								if (!select) return null;

								return {
									options: Array.from(select.options).map(opt => ({
										text: opt.text, //do not trim, because we are doing exact match in select_dropdown_option
										value: opt.value,
										index: opt.index
									})),
									id: select.id,
									name: select.name
								};
							}
						""",
							dom_element.xpath,
						)

						if options:
							logger.debug(f'Found dropdown in frame {frame_index}')
							logger.debug(f'Dropdown ID: {options["id"]}, Name: {options["name"]}')

							formatted_options = []
							for opt in options['options']:
								# encoding ensures AI uses the exact string in select_dropdown_option
								encoded_text = json.dumps(opt['text'])
								formatted_options.append(f'{opt["index"]}: text={encoded_text}')

							all_options.extend(formatted_options)

					except Exception as frame_e:
						logger.debug(f'Frame {frame_index} evaluation failed: {str(frame_e)}')

					frame_index += 1

				if all_options:
					msg = '\n'.join(all_options)
					msg += '\nUse the exact text string in select_dropdown_option'
					logger.info(msg)
					return ActionResult(extracted_content=msg, include_in_memory=True)
				else:
					msg = 'No options found in any frame for dropdown'
					logger.info(msg)
					return ActionResult(extracted_content=msg, include_in_memory=True)

			except Exception as e:
				logger.error(f'Failed to get dropdown options: {str(e)}')
				msg = f'Error getting options: {str(e)}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action(
			description='Select dropdown option for interactive element index by the text of the option you want to select',
		)
		async def select_dropdown_option(
			index: int,
			text: str,
			browser: BrowserContext,
		) -> ActionResult:
			"""Select dropdown option by the text of the option you want to select"""
			page = await browser.get_current_page()
			selector_map = await browser.get_selector_map()
			dom_element = selector_map[index]

			# Validate that we're working with a select element
			if dom_element.tag_name != 'select':
				logger.error(f'Element is not a select! Tag: {dom_element.tag_name}, Attributes: {dom_element.attributes}')
				msg = f'Cannot select option: Element with index {index} is a {dom_element.tag_name}, not a select'
				return ActionResult(extracted_content=msg, include_in_memory=True)

			logger.debug(f"Attempting to select '{text}' using xpath: {dom_element.xpath}")
			logger.debug(f'Element attributes: {dom_element.attributes}')
			logger.debug(f'Element tag: {dom_element.tag_name}')

			xpath = '//' + dom_element.xpath

			try:
				frame_index = 0
				for frame in page.frames:
					try:
						logger.debug(f'Trying frame {frame_index} URL: {frame.url}')

						# First verify we can find the dropdown in this frame
						find_dropdown_js = """
							(xpath) => {
								try {
									const select = document.evaluate(xpath, document, null,
										XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
									if (!select) return null;
									if (select.tagName.toLowerCase() !== 'select') {
										return {
											error: `Found element but it's a ${select.tagName}, not a SELECT`,
											found: false
										};
									}
									return {
										id: select.id,
										name: select.name,
										found: true,
										tagName: select.tagName,
										optionCount: select.options.length,
										currentValue: select.value,
										availableOptions: Array.from(select.options).map(o => o.text.trim())
									};
								} catch (e) {
									return {error: e.toString(), found: false};
								}
							}
						"""

						dropdown_info = await frame.evaluate(find_dropdown_js, dom_element.xpath)

						if dropdown_info:
							if not dropdown_info.get('found'):
								logger.error(f'Frame {frame_index} error: {dropdown_info.get("error")}')
								continue

							logger.debug(f'Found dropdown in frame {frame_index}: {dropdown_info}')

							# "label" because we are selecting by text
							# nth(0) to disable error thrown by strict mode
							# timeout=1000 because we are already waiting for all network events, therefore ideally we don't need to wait a lot here (default 30s)
							selected_option_values = (
								await frame.locator('//' + dom_element.xpath).nth(0).select_option(label=text, timeout=1000)
							)

							msg = f'selected option {text} with value {selected_option_values}'
							logger.info(msg + f' in frame {frame_index}')

							return ActionResult(extracted_content=msg, include_in_memory=True)

					except Exception as frame_e:
						logger.error(f'Frame {frame_index} attempt failed: {str(frame_e)}')
						logger.error(f'Frame type: {type(frame)}')
						logger.error(f'Frame URL: {frame.url}')

					frame_index += 1

				msg = f"Could not select option '{text}' in any frame"
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)

			except Exception as e:
				msg = f'Selection failed: {str(e)}'
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

	def action(self, description: str, **kwargs):
		"""Decorator for registering custom actions

		@param description: Describe the LLM what the function does (better description == better function calling)
		"""
		return self.registry.action(description, **kwargs)

	@time_execution_async('--multi-act')
	async def multi_act(
		self,
		actions: list[ActionModel],
		browser_context: BrowserContext,
		check_break_if_paused: Callable[[], bool],
		check_for_new_elements: bool = True,
		page_extraction_llm: Optional[BaseChatModel] = None,
		sensitive_data: Optional[Dict[str, str]] = None,
	) -> list[ActionResult]:
		"""Execute multiple actions"""
		# Initialize timing variables
		multi_act_start_time = time.time()
		session_setup_duration = 0
		remove_highlights_duration = 0
		individual_action_durations = []
		get_state_durations = []
		
		logger.info(json.dumps({
			"event": "multi_act_start",
			"action_count": len(actions),
			"timestamp": multi_act_start_time
		}))
		
		results = []

		session_start = time.time()
		session = await browser_context.get_session()
		cached_selector_map = session.cached_state.selector_map
		cached_path_hashes = set(e.hash.branch_path_hash for e in cached_selector_map.values())
		session_setup_duration = time.time() - session_start
		
		logger.info(json.dumps({
			"event": "session_setup_complete",
			"duration_ms": round(session_setup_duration * 1000, 1)
		}))

		check_break_if_paused()

		# remove_highlights_start = time.time()
		# await browser_context.remove_highlights()
		# remove_highlights_duration = time.time() - remove_highlights_start
		
		# logger.info(json.dumps({
		# 	"event": "remove_highlights_complete",
		# 	"duration_ms": round(remove_highlights_duration * 1000, 1)
		# }))

		for i, action in enumerate(actions):
			check_break_if_paused()

			# ambar - several steps are only one action long. we should be able to guarantee fresh state at this point.
			# however, we seem to be doing better when we allow state updates on the zeroth action, similar to pre-action updates.
			# so that's the same difference -- we need to be able to NOT update state on the zeroth action.
			if action.get_index() is not None and i != 0:
				get_state_start = time.time()
				new_state = await browser_context.get_state()
				get_state_duration = time.time() - get_state_start
				get_state_durations.append(get_state_duration)
				
				logger.info(json.dumps({
					"event": "get_state_for_element_check",
					"action_index": i,
					"duration_ms": round(get_state_duration * 1000, 1)
				}))
				
				new_path_hashes = set(e.hash.branch_path_hash for e in new_state.selector_map.values())
				if check_for_new_elements and not new_path_hashes.issubset(cached_path_hashes):
					# next action requires index but there are new elements on the page
					logger.info(f'Something new appeared after action {i} / {len(actions)}')
					break

			check_break_if_paused()

			action_start = time.time()
			results.append(await self.act(action, browser_context, page_extraction_llm, sensitive_data))
			action_duration = time.time() - action_start
			individual_action_durations.append(action_duration)

			# Extract actual action name from the action model
			action_name = None
			for name, params in action.model_dump(exclude_unset=True).items():
				if params is not None:
					action_name = name
					break
			
			logger.info(json.dumps({
				"event": "individual_action_complete",
				"action_index": i,
				"action_name": action_name or "unknown",
				"action_type": type(action).__name__,
				"duration_ms": round(action_duration * 1000, 1),
				"is_done": results[-1].is_done,
				"has_error": bool(results[-1].error)
			}))
			
			if results[-1].is_done or results[-1].error or i == len(actions) - 1:
				break

			await asyncio.sleep(browser_context.config.wait_between_actions)
			# hash all elements. if it is a subset of cached_state its fine - else break (new elements on page)

		# Calculate comprehensive timing summary
		total_multi_act_duration = time.time() - multi_act_start_time
		total_action_time = sum(individual_action_durations)
		total_get_state_time = sum(get_state_durations)
		overhead_time = total_multi_act_duration - session_setup_duration - total_action_time - total_get_state_time
		
		logger.info(json.dumps({
			"event": "multi_act_complete",
			"timing": {
				"total_ms": round(total_multi_act_duration * 1000, 1),
				"session_setup_ms": round(session_setup_duration * 1000, 1),
				"total_actions_ms": round(total_action_time * 1000, 1),
				"total_get_state_ms": round(total_get_state_time * 1000, 1),
				"overhead_ms": round(overhead_time * 1000, 1)
			},
			"actions_executed": len(individual_action_durations),
			"actions_planned": len(actions),
			"success_count": len([r for r in results if not r.error and not r.is_done]),
			"done_count": len([r for r in results if r.is_done]),
			"error_count": len([r for r in results if r.error]),
			"get_state_calls": len(get_state_durations),
			"action_durations_ms": [round(d * 1000, 1) for d in individual_action_durations]
		}))

		return results

	@time_execution_sync('--act')
	async def act(
		self,
		action: ActionModel,
		browser_context: BrowserContext,
		page_extraction_llm: Optional[BaseChatModel] = None,
		sensitive_data: Optional[Dict[str, str]] = None,
	) -> ActionResult:
		"""Execute an action"""
		try:
			for action_name, params in action.model_dump(exclude_unset=True).items():
				if params is not None:
					result = await self.registry.execute_action(
						action_name,
						params,
						browser=browser_context,
						page_extraction_llm=page_extraction_llm,
						sensitive_data=sensitive_data,
					)
					if isinstance(result, str):
						return ActionResult(extracted_content=result)
					elif isinstance(result, ActionResult):
						return result
					elif result is None:
						return ActionResult()
					else:
						raise ValueError(f'Invalid action result type: {type(result)} of {result}')
			return ActionResult()
		except Exception as e:
			raise e
