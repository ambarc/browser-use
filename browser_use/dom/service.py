import logging
import json
from importlib import resources
from typing import Optional

from playwright.async_api import Page

import time

from browser_use.dom.history_tree_processor.view import Coordinates
from browser_use.dom.views import (
	CoordinateSet,
	DOMBaseNode,
	DOMElementNode,
	DOMState,
	DOMTextNode,
	SelectorMap,
	ViewportInfo,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class DomService:
	def __init__(self, page: Page):
		self.page = page
		self.xpath_cache = {}
		# logger.debug("DomService initialized with page")

	# region - Clickable elements
	async def get_clickable_elements(
		self,
		highlight_elements: bool = True,
		focus_element: int = -1,
		viewport_expansion: int = 1,
	) -> DOMState:
		get_clickable_start = time.time()
		
		logger.info(json.dumps({
			"event": "get_clickable_elements_start",
			"highlight_elements": highlight_elements,
			"focus_element": focus_element,
			"viewport_expansion": viewport_expansion
		}))
		
		# Build DOM tree
		build_tree_start = time.time()
		element_tree = await self._build_dom_tree(highlight_elements, focus_element, viewport_expansion)
		build_tree_duration = time.time() - build_tree_start
		element_count = self._count_elements(element_tree)
		
		logger.info(json.dumps({
			"event": "get_clickable_elements_dom_tree_complete",
			"duration_ms": round(build_tree_duration * 1000, 1),
			"element_count": element_count
		}))
		
		# Create selector map
		selector_map_start = time.time()
		selector_map = self._create_selector_map(element_tree)
		selector_map_duration = time.time() - selector_map_start
		
		logger.info(json.dumps({
			"event": "get_clickable_elements_selector_map_complete",
			"duration_ms": round(selector_map_duration * 1000, 1),
			"selector_count": len(selector_map)
		}))
		
		# Create final state
		state_creation_start = time.time()
		dom_state = DOMState(element_tree=element_tree, selector_map=selector_map)
		state_creation_duration = time.time() - state_creation_start
		
		total_duration = time.time() - get_clickable_start
		logger.info(json.dumps({
			"event": "get_clickable_elements_complete",
			"total_duration_ms": round(total_duration * 1000, 1),
			"timing_breakdown": {
				"build_tree_ms": round(build_tree_duration * 1000, 1),
				"selector_map_ms": round(selector_map_duration * 1000, 1),
				"state_creation_ms": round(state_creation_duration * 1000, 1)
			},
			"stats": {
				"element_count": element_count,
				"selector_count": len(selector_map),
				"highlight_enabled": highlight_elements
			}
		}))

		return dom_state

	def _count_elements(self, node: DOMBaseNode) -> int:
		"""Count elements for debugging"""
		if not isinstance(node, DOMElementNode):
			return 0
		return 1 + sum(self._count_elements(child) for child in node.children)

	async def _build_dom_tree(
		self,
		highlight_elements: bool,
		focus_element: int,
		viewport_expansion: int,
	) -> DOMElementNode:
		start_time = time.time()
		
		# Read JavaScript code
		js_read_start = time.time()
		js_code = resources.read_text('browser_use.dom', 'buildDomTree-top.js')
		js_read_duration = time.time() - js_read_start
		
		logger.info(json.dumps({
			"event": "build_dom_tree_js_loaded",
			"duration_ms": round(js_read_duration * 1000, 1),
			"js_size_chars": len(js_code)
		}))

		args = {
			'doHighlightElements': highlight_elements,
			'focusHighlightIndex': focus_element,
			'viewportExpansion': viewport_expansion,
		}

		# Execute JavaScript in browser
		js_execute_start = time.time()
		# ambar - this ranges from 2-6s on ERPs, and gets called a lot. 
		# not re-evaluting this also leads to clicks and inputs failing because of elements that couldn't be located. 
		# we'd be fine if we could skip the extra evaluation while still being able to locate elements.
		eval_page = json.loads(await self.page.evaluate(js_code, args))
		js_execute_duration = time.time() - js_execute_start
		data_size = len(str(eval_page))
		
		logger.info(json.dumps({
			"event": "build_dom_tree_js_executed",
			"duration_ms": round(js_execute_duration * 1000, 1),
			"data_size_chars": data_size,
			"data_size_mb": round(data_size / (1024 * 1024), 2)
		}))
		
		# Parse the DOM data
		parse_start = time.time()
		html_to_dict = self._parse_node(eval_page)
		parse_duration = time.time() - parse_start
		
		logger.info(json.dumps({
			"event": "build_dom_tree_parsed",
			"duration_ms": round(parse_duration * 1000, 1)
		}))

		if html_to_dict is None or not isinstance(html_to_dict, DOMElementNode):
			logger.error("Failed to parse HTML to dictionary")
			raise ValueError('Failed to parse HTML to dictionary')

		build_duration = time.time() - start_time
		logger.info(json.dumps({
			"event": "build_dom_tree_complete",
			"total_duration_ms": round(build_duration * 1000, 1),
			"timing_breakdown": {
				"js_read_ms": round(js_read_duration * 1000, 1),
				"js_execute_ms": round(js_execute_duration * 1000, 1),
				"parse_ms": round(parse_duration * 1000, 1)
			},
			"data_size_chars": data_size,
			"data_size_mb": round(data_size / (1024 * 1024), 2)
		}))
		return html_to_dict

	def _create_selector_map(self, element_tree: DOMElementNode) -> SelectorMap:
		selector_map = {}

		def process_node(node: DOMBaseNode):
			if isinstance(node, DOMElementNode):
				if node.highlight_index is not None:
					selector_map[node.highlight_index] = node
					# logger.debug(f"Added to selector map: idx={node.highlight_index}, tag={node.tag_name}, xpath={node.xpath}")

				for child in node.children:
					process_node(child)

		process_node(element_tree)
		# logger.debug(f"Created selector map with {len(selector_map)} elements")
		return selector_map

	def _parse_node(
		self,
		node_data: dict,
		parent: Optional[DOMElementNode] = None,
	) -> Optional[DOMBaseNode]:
		if not node_data:
			return None

		if node_data.get('type') == 'TEXT_NODE':
			text_node = DOMTextNode(
				text=node_data['text'],
				is_visible=node_data['isVisible'],
				parent=parent,
			)
			return text_node

		tag_name = node_data['tagName']
		
		# Log frame elements
		#if tag_name in ['iframe', 'frame', 'frameset']:
			# logger.debug(f"Processing {tag_name} element with xpath: {node_data.get('xpath')}")
		#	if 'frameContent' in node_data:
		#		logger.debug(f"Frame content: {node_data['frameContent']}")

		# Parse coordinates if they exist
		viewport_coordinates = None
		page_coordinates = None
		viewport_info = None

		if 'viewportCoordinates' in node_data:
			viewport_coordinates = CoordinateSet(
				top_left=Coordinates(**node_data['viewportCoordinates']['topLeft']),
				top_right=Coordinates(**node_data['viewportCoordinates']['topRight']),
				bottom_left=Coordinates(**node_data['viewportCoordinates']['bottomLeft']),
				bottom_right=Coordinates(**node_data['viewportCoordinates']['bottomRight']),
				center=Coordinates(**node_data['viewportCoordinates']['center']),
				width=node_data['viewportCoordinates']['width'],
				height=node_data['viewportCoordinates']['height'],
			)

		if 'pageCoordinates' in node_data:
			page_coordinates = CoordinateSet(
				top_left=Coordinates(**node_data['pageCoordinates']['topLeft']),
				top_right=Coordinates(**node_data['pageCoordinates']['topRight']),
				bottom_left=Coordinates(**node_data['pageCoordinates']['bottomLeft']),
				bottom_right=Coordinates(**node_data['pageCoordinates']['bottomRight']),
				center=Coordinates(**node_data['pageCoordinates']['center']),
				width=node_data['pageCoordinates']['width'],
				height=node_data['pageCoordinates']['height'],
			)

		if 'viewport' in node_data:
			viewport_info = ViewportInfo(
				scroll_x=node_data['viewport']['scrollX'],
				scroll_y=node_data['viewport']['scrollY'],
				width=node_data['viewport']['width'],
				height=node_data['viewport']['height'],
			)

		element_node = DOMElementNode(
			tag_name=tag_name,
			xpath=node_data['xpath'],
			attributes=node_data.get('attributes', {}),
			children=[],
			is_visible=node_data.get('isVisible', False),
			is_interactive=node_data.get('isInteractive', False),
			is_top_element=node_data.get('isTopElement', False),
			highlight_index=node_data.get('highlightIndex'),
			shadow_root=node_data.get('shadowRoot', False),
			parent=parent,
			frame_info=node_data.get('frameInfo'),
			is_frame_boundary=node_data.get('isFrameBoundary', False),
			viewport_coordinates=viewport_coordinates,
			page_coordinates=page_coordinates,
			viewport_info=viewport_info,
		)

		children: list[DOMBaseNode] = []
		for child in node_data.get('children', []):
			if child is not None:
				child_node = self._parse_node(child, parent=element_node)
				if child_node is not None:
					children.append(child_node)

		element_node.children = children

		return element_node

	# endregion
