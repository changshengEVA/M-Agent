#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
瀹炰綋瑙ｆ瀽鏈嶅姟涓诲叆鍙?

鑱岃矗锛?
- 鍚姩闃舵锛氫粠 KG 閲嶅缓 EntityLibrary锛堟淳鐢熺储寮曪級
- 杩愯闃舵锛氭帴鏀舵柊 entity_id锛岃皟鐢ㄨВ鏋愮瓥鐣ヨ繘琛屽垽瀹?
- 鏍规嵁鍒ゅ畾缁撴灉锛氭洿鏂?EntityLibrary锛屽湪蹇呰鏃惰皟鐢?kg_core 杩涜瀹炰綋鍚堝苟

璁捐鍘熷垯锛?
- 鍒ゅ畾锛坮esolve锛変笌鎵ц锛坅pply锛夊垎绂?
- 涓嶅寘鍚叿浣撳垽瀹氱瓥鐣ラ€昏緫
- 涓嶇洿鎺ユ搷浣?KG 瀛樺偍
"""

import logging
import time
from typing import Dict, Any, Optional, List, Callable, TYPE_CHECKING
from dataclasses import dataclass, field

try:
    # 灏濊瘯鐩稿瀵煎叆锛堝綋浣滀负鍖呯殑涓€閮ㄥ垎鏃讹級
    from .decision import ResolutionDecision, ResolutionType
    from .library import EntityLibrary, EntityRecord
    from .strategies import ResolutionStrategy, AliasThenEmbeddingLLMStrategy
except ImportError:
    # 鍥為€€鍒扮洿鎺ュ鍏ワ紙褰撶洿鎺ヨ繍琛屾椂锛?
    from decision import ResolutionDecision, ResolutionType
    from library import EntityLibrary, EntityRecord
    from strategies import ResolutionStrategy, AliasThenEmbeddingLLMStrategy

# 瀵煎叆浜嬩欢鎬荤嚎鐩稿叧妯″潡
try:
    from m_agent.memory.memory_core.services_bank.base_service import BaseService
    from m_agent.memory.memory_core.system.event_types import EventType
except ImportError:
    # 鍥為€€鍒扮浉瀵瑰鍏?
    import sys
    sys.path.append("..")
    from base_service import BaseService
    from system.event_types import EventType

# 绫诲瀷妫€鏌ユ椂瀵煎叆KGBase锛岄伩鍏嶅惊鐜鍏?
if TYPE_CHECKING:
    from m_agent.memory.memory_core.core.kg_base import KGBase
else:
    # 杩愯鏃朵娇鐢ㄥ瓧绗︿覆绫诲瀷鎻愮ず
    KGBase = "KGBase"  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """瀹炰綋瑙ｆ瀽瀹屾暣缁撴灉"""
    decision: ResolutionDecision  # 瑙ｆ瀽鍒ゅ畾
    applied: bool = False  # 鏄惁宸插簲鐢ㄥ垽瀹氱粨鏋?
    library_updated: bool = False  # EntityLibrary 鏄惁宸叉洿鏂?
    kg_operation_performed: bool = False  # 鏄惁鎵ц浜?KG 鎿嶄綔
    kg_operation_result: Optional[Dict[str, Any]] = None  # KG 鎿嶄綔缁撴灉
    error: Optional[str] = None  # 閿欒淇℃伅
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the result to a dictionary payload."""
        return {
            "decision": self.decision.to_dict(),
            "applied": self.applied,
            "library_updated": self.library_updated,
            "kg_operation_performed": self.kg_operation_performed,
            "kg_operation_result": self.kg_operation_result,
            "error": self.error,
            "success": self.error is None,
        }


class EntityResolutionService(BaseService):
    """Entity resolution service."""
    
    def __init__(
        self,
        llm_func: Callable[[str], str],
        embed_func: Callable[[str], List[float]],
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True,
        data_path: Optional[str] = None
    ):
        """
        Initialize the entity resolution service.

        Args:
            llm_func: LLM callable used for disambiguation.
            embed_func: Embedding callable used for candidate matching.
            similarity_threshold: Similarity threshold for vector matching.
            top_k: Number of top candidates to keep.
            use_threshold: Whether to enforce threshold-based filtering.
            data_path: Optional path for persisting the entity library.
        """
        # 鍒濆鍖栧疄浣撳簱锛屼紶鍏mbed_func鍜宒ata_path
        self.entity_library = EntityLibrary(embed_func=embed_func, data_path=data_path)
        self.strategies: List[ResolutionStrategy] = []
        self.llm_func = llm_func
        self.embed_func = embed_func
        self.data_path = data_path
        
        # 鍒濆鍖栭粯璁ょ瓥鐣ワ紙鍗曚竴绛栫暐锛?
        self._init_default_strategy(
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold
        )
        
        logger.info("鍒濆鍖?EntityResolutionService")
    
    def get_subscribed_events(self):
        """
        杩斿洖鐩戝惉鐨?EventType 鍒楄〃
        
        瀹炰綋瑙ｆ瀽鏈嶅姟闇€瑕佺洃鍚疄浣撴坊鍔犮€佸悎骞跺拰閲嶅懡鍚嶄簨浠讹紝浠ヤ究鏇存柊 EntityLibrary銆?
        """
        return [
            EventType.ENTITY_ADDED,
            EventType.ENTITY_MERGED,
            EventType.ENTITY_RENAMED,
        ]
    
    def handle_event(self, event_type: str, payload: dict) -> None:
        """
        澶勭悊浜嬩欢
        
        Args:
            event_type: 浜嬩欢绫诲瀷瀛楃涓?
            payload: 浜嬩欢璐熻浇瀛楀吀
        """
        self._log_event_handling(event_type, payload)
        
        if event_type == EventType.ENTITY_MERGED:
            source_id = payload.get("source_id")
            target_id = payload.get("target_id")
            if source_id and target_id:
                self.on_entity_merged(source_id, target_id)
        elif event_type == EventType.ENTITY_ADDED:
            entity_id = payload.get("entity_id")
            if entity_id:
                self.on_entity_added(entity_id)
        elif event_type == EventType.ENTITY_RENAMED:
            old_id = payload.get("old_id")
            new_id = payload.get("new_id")
            if old_id and new_id:
                self.on_entity_renamed(old_id, new_id)
        else:
            logger.debug(f"EntityResolutionService 蹇界暐鏈鐞嗕簨浠? {event_type}")
    
    def on_entity_merged(self, source_id: str, target_id: str, **kwargs) -> None:
        """
        鐩戝惉瀹炰綋鍚堝苟浜嬩欢
        
        褰?MemoryCore 鎵ц瀹炰綋鍚堝苟鏃惰皟鐢ㄦ鏂规硶锛岀敤浜庢洿鏂?EntityLibrary
        
        Args:
            source_id: 婧愬疄浣揑D锛堝皢琚悎骞讹級
            target_id: 鐩爣瀹炰綋ID锛堜繚鐣欙級
            **kwargs: 鍏朵粬鍙傛暟锛堝鍚堝苟缁撴灉绛夛級
        """
        logger.info(f"鏀跺埌瀹炰綋鍚堝苟浜嬩欢: {source_id} -> {target_id}")
        
        try:
            # Step 1: 鏀堕泦婧愬疄浣撶殑鎵€鏈夊埆鍚嶏紙鍦ㄥ垹闄や箣鍓嶏級
            source_aliases = []
            if source_id in self.entity_library.entities:
                # 鑾峰彇婧愬疄浣撶殑鎵€鏈夊悕绉帮紙鍖呮嫭瑙勮寖鍚嶇О鍜屽埆鍚嶏級
                source_record = self.entity_library.entities[source_id]
                source_aliases = source_record.get_all_names()
                logger.debug(f"婧愬疄浣?{source_id} 鐨勫埆鍚? {source_aliases}")
            
            # Step 2: 濡傛灉 source_id 瀛樺湪浜?EntityLibrary.entities锛屽垹闄ょ浉鍏虫暟鎹?
            if source_id in self.entity_library.entities:
                logger.info(f"Removing source entity state for {source_id}")
                
                # 鑾峰彇婧愬疄浣撶殑鎵€鏈夊悕绉帮紙鍖呮嫭瑙勮寖鍚嶇О鍜屽埆鍚嶏級
                source_record = self.entity_library.entities[source_id]
                source_names = source_record.get_all_names()
                
                # 浠?name_to_entity 鏄犲皠涓垹闄ゆ簮瀹炰綋鐨勬墍鏈夊悕绉?
                names_removed = 0
                for name in source_names:
                    if name in self.entity_library.name_to_entity:
                        if self.entity_library.name_to_entity[name] == source_id:
                            del self.entity_library.name_to_entity[name]
                            names_removed += 1
                            logger.debug(f"浠?name_to_entity 涓垹闄ゅ悕绉版槧灏? {name} -> {source_id}")
                
                logger.info(f"Removed {names_removed} name mappings for {source_id}")
                
                # 浠?embeddings 涓垹闄?source_id
                if source_id in self.entity_library.embeddings:
                    del self.entity_library.embeddings[source_id]
                    logger.debug(f"鍒犻櫎宓屽叆鍚戦噺: {source_id}")
                
                # 浠?entities 涓垹闄?source_id
                del self.entity_library.entities[source_id]
                logger.debug(f"鍒犻櫎瀹炰綋璁板綍: {source_id}")
            
            # Step 3: 纭繚 target_id 瀛樺湪锛堝鏋滀笉瀛樺湪鍏?add_entity锛?
            if target_id not in self.entity_library.entities:
                logger.info(f"Target entity {target_id} was missing; creating it first")
                add_success = self.entity_library.add_entity(
                    entity_id=target_id,
                    canonical_name=target_id,
                    metadata={
                        "added_via": "entity_merge_event",
                        "source_entity": source_id,
                        "timestamp": time.time()
                    }
                )
                
                if not add_success:
                    logger.warning(f"鏃犳硶娣诲姞鐩爣瀹炰綋鍒?EntityLibrary: {target_id}")
                    return
            
            # Step 4: 灏嗘簮瀹炰綋鐨勬墍鏈夊埆鍚嶉噸鏂版槧灏勫埌鐩爣瀹炰綋
            aliases_added = 0
            aliases_failed = 0
            
            for alias in source_aliases:
                # 璺宠繃鐩爣瀹炰綋鏈韩锛堝鏋滃埆鍚嶄笌鐩爣瀹炰綋ID鐩稿悓锛?
                if alias == target_id:
                    logger.debug(f"璺宠繃鍒悕 {alias}锛屽洜涓哄畠涓庣洰鏍囧疄浣揑D鐩稿悓")
                    continue
                    
                # 娣诲姞鍒悕鍒扮洰鏍囧疄浣?
                success = self.entity_library.add_alias(
                    entity_id=target_id,
                    alias=alias
                )
                
                if success:
                    aliases_added += 1
                    logger.debug(f"鎴愬姛娣诲姞鍒悕: {alias} -> {target_id}")
                else:
                    aliases_failed += 1
                    logger.warning(f"Failed to add alias mapping {alias} -> {target_id}")
            
            logger.info(f"Alias remap finished: added={aliases_added}, failed={aliases_failed}")
            
            # Step 5: 楠岃瘉鏈€缁堜笉鍙橀噺
            if source_id in self.entity_library.entities:
                logger.error(f"Merge verification failed: source entity still exists: {source_id}")
            else:
                logger.debug(f"Merge verification passed for removed source entity {source_id}")
            
            # Step 6: 淇濆瓨 EntityLibrary 鍒扮鐩?
            if self.data_path:
                save_success = self.entity_library.save_to_path(self.data_path)
                if save_success:
                    logger.info(f"EntityLibrary 宸蹭繚瀛樺埌纾佺洏: {self.data_path}")
                else:
                    logger.error(f"Failed to save EntityLibrary to disk: {self.data_path}")
            else:
                logger.warning("No data_path configured; skipping EntityLibrary persistence")
                
        except Exception as e:
            logger.error(f"Error while handling entity merge event: {e}")
    
    def on_entity_added(self, entity_id: str) -> None:
        """
        鐩戝惉瀹炰綋娣诲姞浜嬩欢
        
        褰?KG 涓坊鍔犳柊瀹炰綋鏃讹紝浠呭悓姝ョ姸鎬佸埌 EntityLibrary锛屼笉瑙﹀彂瑙ｆ瀽銆?
        
        Args:
            entity_id: 鏂版坊鍔犵殑瀹炰綋ID
        """
        logger.info(f"鏀跺埌瀹炰綋娣诲姞浜嬩欢锛屽悓姝ュ埌 EntityLibrary: {entity_id}")
        
        try:
            # 妫€鏌ュ疄浣撴槸鍚﹀凡鍦?EntityLibrary 涓?
            if not self.entity_library.entity_exists(entity_id):
                # 娣诲姞瀹炰綋鍒?EntityLibrary
                success = self.entity_library.add_entity(
                    entity_id=entity_id,
                    canonical_name=entity_id,
                    metadata={
                        "added_via": "entity_added_event",
                        "timestamp": time.time()
                    }
                )
                
                if success:
                    # 鏍囪瀹炰綋涓烘湭瑙ｆ瀽鐘舵€?
                    record = self.entity_library.get_entity(entity_id)
                    if record:
                        record.mark_as_unresolved()
                        logger.info(f"瀹炰綋宸叉坊鍔犲埌 EntityLibrary 骞舵爣璁颁负鏈В鏋? {entity_id}")
                    else:
                        logger.warning(f"鏃犳硶鑾峰彇鏂版坊鍔犵殑瀹炰綋璁板綍: {entity_id}")
                else:
                    logger.warning(f"鏃犳硶娣诲姞瀹炰綋鍒?EntityLibrary: {entity_id}")
            else:
                # 瀹炰綋宸插瓨鍦紝纭繚鏍囪涓烘湭瑙ｆ瀽鐘舵€?
                record = self.entity_library.get_entity(entity_id)
                if record:
                    record.mark_as_unresolved()
                    logger.info(f"瀹炰綋宸插瓨鍦紝鏍囪涓烘湭瑙ｆ瀽: {entity_id}")
                else:
                    logger.warning(f"瀹炰綋瀛樺湪浣嗘棤娉曡幏鍙栬褰? {entity_id}")
                
        except Exception as e:
            logger.error(f"澶勭悊瀹炰綋娣诲姞浜嬩欢鏃跺嚭閿?{entity_id}: {e}")
    
    def on_entity_renamed(self, old_id: str, new_id: str) -> None:
        """
        鐩戝惉瀹炰綋閲嶅懡鍚嶄簨浠?
        
        褰?KG 涓疄浣撻噸鍛藉悕鏃讹紝鏇存柊 EntityLibrary 浠ヤ繚鎸佸悓姝ャ€?
        
        Args:
            old_id: 鍘熷疄浣?ID
            new_id: 鏂板疄浣?ID
        """
        logger.info(f"鏀跺埌瀹炰綋閲嶅懡鍚嶄簨浠? {old_id} -> {new_id}")
        
        # 妫€鏌ュ師瀹炰綋鏄惁鍦?EntityLibrary 涓?
        if old_id not in self.entity_library.entities:
            logger.debug(f"鍘熷疄浣?{old_id} 涓嶅湪 EntityLibrary 涓紝璺宠繃")
            return
        
        try:
            # 鑾峰彇鍘熷疄浣撹褰?
            old_record = self.entity_library.entities[old_id]
            
            # 鍒涘缓鏂板疄浣撹褰曪紝缁ф壙鍘熻褰曠殑鎵€鏈夊睘鎬?
            new_record = EntityRecord(
                entity_id=new_id,
                canonical_name=new_id,  # 浣跨敤鏂癐D浣滀负瑙勮寖鍖栧悕绉?
                aliases=old_record.aliases.copy(),
                embedding=old_record.embedding,
                entity_type=old_record.entity_type,
                metadata=old_record.metadata.copy(),
                resolved=old_record.resolved,
                last_decision=old_record.last_decision
            )
            
            # 灏?old_id 娣诲姞涓烘柊瀹炰綋鐨勫埆鍚?
            new_record.aliases.append(old_id)
            
            # 浠庣储寮曚腑绉婚櫎鍘熷疄浣?
            del self.entity_library.entities[old_id]
            
            # 浠庡悕绉版槧灏勪腑绉婚櫎鍘熷疄浣撶殑鎵€鏈夊悕绉?
            for name in old_record.get_all_names():
                if name in self.entity_library.name_to_entity and self.entity_library.name_to_entity[name] == old_id:
                    del self.entity_library.name_to_entity[name]
            
            # 浠庡祵鍏ュ悜閲忔槧灏勪腑绉婚櫎鍘熷疄浣?
            if old_id in self.entity_library.embeddings:
                del self.entity_library.embeddings[old_id]
            
            # 娣诲姞鏂板疄浣撳埌绱㈠紩
            self.entity_library.entities[new_id] = new_record
            
            # 寤虹珛鏂板疄浣撶殑鍚嶇О鏄犲皠
            for name in new_record.get_all_names():
                self.entity_library.name_to_entity[name] = new_id
            
            # 娣诲姞宓屽叆鍚戦噺
            if new_record.embedding:
                self.entity_library.embeddings[new_id] = new_record.embedding
            
            logger.info(f"EntityLibrary 鏇存柊鎴愬姛: {old_id} -> {new_id}")
            
        except Exception as e:
            logger.error(f"澶勭悊瀹炰綋閲嶅懡鍚嶄簨浠跺け璐?{old_id} -> {new_id}: {e}")
    
    def _init_default_strategy(
        self,
        similarity_threshold: float = 0.7,
        top_k: int = 3,
        use_threshold: bool = True
    ) -> None:
        """Initialize the default strategy list."""
        # 鍒涘缓鍗曚竴绛栫暐锛氬埆鍚嶁啋鍚戦噺鐩镐技搴︹啋LLM鍒ゅ埆
        # 浣跨敤涓庢枃浠堕《閮ㄧ浉鍚岀殑瀵煎叆妯″紡
        try:
            from .strategies import AliasThenEmbeddingLLMStrategy
        except ImportError:
            from strategies import AliasThenEmbeddingLLMStrategy
        
        strategy = AliasThenEmbeddingLLMStrategy(
            llm_func=self.llm_func,
            embed_func=self.embed_func,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            use_threshold=use_threshold
        )
        
        self.strategies = [strategy]
        logger.info(f"鍒濆鍖栭粯璁ょ瓥鐣? {strategy.name}")
    
    def add_strategy(self, strategy: ResolutionStrategy) -> None:
        """娣诲姞瑙ｆ瀽绛栫暐"""
        self.strategies.append(strategy)
        logger.info(f"娣诲姞瑙ｆ瀽绛栫暐: {strategy.name}")
    
    def set_strategies(self, strategies: List[ResolutionStrategy]) -> None:
        """璁剧疆瑙ｆ瀽绛栫暐鍒楄〃锛堟浛鎹㈢幇鏈夌瓥鐣ワ級"""
        self.strategies = strategies
        logger.info(f"璁剧疆瑙ｆ瀽绛栫暐: {[s.name for s in self.strategies]}")
    
    
    def resolve_entity(
        self, 
        entity_id: str, 
        context: Optional[Dict[str, Any]] = None
    ) -> ResolutionDecision:
        """
        瑙ｆ瀽瀹炰綋
        
        Args:
            entity_id: 寰呰В鏋愮殑瀹炰綋ID
            context: 涓婁笅鏂囦俊鎭紙濡傚祵鍏ュ悜閲忕瓑锛?
            
        Returns:
            ResolutionDecision 鍒ゅ畾缁撴灉
        """
        logger.info(f"寮€濮嬭В鏋愬疄浣? {entity_id}")
        
        if not self.strategies:
            logger.warning("鏈厤缃В鏋愮瓥鐣ワ紝杩斿洖鏂板缓瀹炰綋")
            return ResolutionDecision(
                resolution_type=ResolutionType.NEW_ENTITY,
                source_entity_id=entity_id,
                strategy_name="NoStrategy",
                confidence=0.0,
                evidence={"error": "no_strategies_configured"},
                timestamp=time.time()
            )
        
        # 浣跨敤绗竴涓瓥鐣ヨ繘琛岃В鏋愶紙閫氬父鏄粍鍚堢瓥鐣ワ級
        strategy = self.strategies[0]
        
        try:
            decision = strategy.resolve(entity_id, self.entity_library, context)
            decision.timestamp = time.time()  # 璁剧疆鏃堕棿鎴?
            
            logger.info(f"瀹炰綋瑙ｆ瀽瀹屾垚: {entity_id} -> {decision.resolution_type.value}")
            if decision.is_same_as_existing():
                logger.info(f"  鐩爣瀹炰綋: {decision.target_entity_id}, 缃俊搴? {decision.confidence:.2f}")
            
            return decision
            
        except Exception as e:
            logger.error(f"瑙ｆ瀽瀹炰綋澶辫触 {entity_id}: {e}")
            return ResolutionDecision(
                resolution_type=ResolutionType.NEW_ENTITY,
                source_entity_id=entity_id,
                strategy_name=strategy.name,
                confidence=0.0,
                evidence={"error": str(e)},
                timestamp=time.time()
            )
    
    def get_library_stats(self) -> Dict[str, Any]:
        """Return entity library statistics."""
        return self.entity_library.get_stats()
    
    def clear_library(self) -> None:
        """Clear the entity library."""
        self.entity_library.clear()
        logger.info("Cleared entity library")
    
    def save_library(self) -> bool:
        """
        淇濆瓨瀹炰綋搴撴暟鎹埌鏂囦欢
        
        Returns:
            鏄惁鎴愬姛淇濆瓨
        """
        if not self.data_path:
            logger.warning("No data_path configured; cannot save entity library")
            return False
        
        try:
            success = self.entity_library.save_to_path(self.data_path)
            if success:
                logger.info(f"Saved entity library to {self.data_path}")
            else:
                logger.warning(f"Failed to save entity library to {self.data_path}")
            return success
        except Exception as e:
            logger.error(f"淇濆瓨瀹炰綋搴撴暟鎹椂鍑洪敊: {e}")
            return False
    
    def resolve_unresolved_entities(self) -> List[ResolutionDecision]:
        """
        鎵归噺瑙ｆ瀽鏈В鏋愮殑瀹炰綋
        
        閬嶅巻 EntityLibrary 涓墍鏈夊疄浣擄紝鎵惧埌 resolved == False 鐨勫疄浣擄紝
        璋冪敤绛栫暐杩涜瑙ｆ瀽锛屼繚瀛樿В鏋愮粨鏋滐紝骞舵爣璁颁负宸茶В鏋愩€?
        
        杩斿洖瑙ｆ瀽寤鸿闆嗗悎锛坧roposal锛夛紝涓嶆墽琛屼换浣?KG 淇敼銆?
        
        Returns:
            List[ResolutionDecision] 瑙ｆ瀽寤鸿鍒楄〃
        """
        logger.info("Resolving unresolved entities in batch")
        
        decisions = []
        
        if not self.strategies:
            logger.warning("鏈厤缃В鏋愮瓥鐣ワ紝鏃犳硶瑙ｆ瀽")
            return decisions
        
        # 浣跨敤绗竴涓瓥鐣ヨ繘琛岃В鏋?
        strategy = self.strategies[0]
        
        # 棣栧厛缁熻鏈В鏋愬疄浣撶殑鎬绘暟
        unresolved_entities = []
        for entity_id, record in self.entity_library.entities.items():
            if not record.resolved:
                unresolved_entities.append((entity_id, record))
        
        total_unresolved = len(unresolved_entities)
        logger.info(f"Found {total_unresolved} unresolved entities to process")
        
        if total_unresolved == 0:
            logger.info("No unresolved entities to process")
            return decisions
        
        # 浣跨敤 tqdm 鏄剧ず杩涘害鏉?
        try:
            from tqdm import tqdm
            
            # 閰嶇疆 tqdm 浠ョ‘淇濊繘搴︽潯鑳芥纭樉绀?
            # 浣跨敤 ascii 杩涘害鏉＄‘淇濆湪涓嶅悓缁堢涓兘鑳芥樉绀?
            # 璁剧疆 mininterval 浠ュ噺灏戝埛鏂伴鐜囷紝閬垮厤涓庢棩蹇楀啿绐?
            tqdm_kwargs = {
                "desc": "瑙ｆ瀽瀹炰綋",
                "unit": "瀹炰綋",
                "total": total_unresolved,
                "ascii": True,  # 浣跨敤 ASCII 瀛楃纭繚鍏煎鎬?
                "mininterval": 0.5,  # 鏈€灏忓埛鏂伴棿闅?
                "bar_format": "{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
            }
            
            # 鍒涘缓杩涘害鏉¤凯浠ｅ櫒
            progress_iterator = tqdm(unresolved_entities, **tqdm_kwargs)
            
        except ImportError:
            logger.warning("tqdm is not installed; using a plain iterator")
            # 鍥為€€鍒扮畝鍗曟灇涓?
            progress_iterator = unresolved_entities
        
        # 浣跨敤杩涘害鏉¤凯浠ｅ櫒閬嶅巻鏈В鏋愬疄浣?
        for entity_id, record in progress_iterator:
            try:
                # 鏇存柊杩涘害鏉℃弿杩帮紝鏄剧ず褰撳墠澶勭悊鐨勫疄浣?
                if hasattr(progress_iterator, "set_description"):
                    progress_iterator.set_description(f"瑙ｆ瀽: {entity_id[:20]}...")
                
                # 璋冪敤绛栫暐杩涜瑙ｆ瀽
                decision = strategy.resolve(entity_id, self.entity_library, context=None)
                decision.timestamp = time.time()
                decision.source_entity_id = entity_id
                
                # 淇濆瓨 decision 鍒?last_decision
                record.last_decision = decision.to_dict()
                
                # 鏍囪涓哄凡瑙ｆ瀽
                record.mark_as_resolved(record.last_decision)
                
                # 娣诲姞鍒拌繑鍥炲垪琛?
                decisions.append(decision)
                
                # 鏇存柊杩涘害鏉″悗鎻忚堪
                if hasattr(progress_iterator, "set_postfix"):
                    result_type = decision.resolution_type.value[:10]
                    progress_iterator.set_postfix(result=result_type, conf=f"{decision.confidence:.2f}")
                
            except Exception as e:
                logger.error(f"瑙ｆ瀽瀹炰綋澶辫触 {entity_id}: {e}")
                # 鍒涘缓涓€涓敊璇喅绛?
                error_decision = ResolutionDecision(
                    resolution_type=ResolutionType.NEW_ENTITY,
                    source_entity_id=entity_id,
                    strategy_name=strategy.name,
                    confidence=0.0,
                    evidence={"error": str(e)},
                    timestamp=time.time()
                )
                decisions.append(error_decision)
                
                # 鏇存柊杩涘害鏉℃樉绀洪敊璇?
                if hasattr(progress_iterator, "set_postfix"):
                    progress_iterator.set_postfix(error="澶辫触")
        
        logger.info(f"Batch entity resolution finished with {len(decisions)} decisions")
        
        # 瑙ｆ瀽瀹屾垚鍚庤嚜鍔ㄥ瓨妗?
        if decisions and self.data_path:
            save_success = self.save_library()
            if save_success:
                logger.info(f"Auto-saved entity library after resolution to {self.data_path}")
            else:
                logger.warning(f"Failed to auto-save entity library after resolution to {self.data_path}")
        
        return decisions
    
    def __str__(self) -> str:
        """Return a readable service summary."""
        stats = self.get_library_stats()
        strategy_names = [s.name for s in self.strategies]
        
        # 缁熻鏈В鏋愬疄浣撴暟閲?
        unresolved_count = sum(1 for record in self.entity_library.entities.values() if not record.resolved)
        
        return f"EntityResolutionService(entities={stats['entity_count']}, unresolved={unresolved_count}, strategies={strategy_names})"


# 渚挎嵎鍑芥暟
def create_default_resolution_service(
    llm_func: Callable[[str], str],
    embed_func: Callable[[str], List[float]],
    similarity_threshold: float = 0.7,
    top_k: int = 3,
    use_threshold: bool = True,
    data_path: Optional[str] = None
) -> EntityResolutionService:
    """
    鍒涘缓榛樿閰嶇疆鐨勫疄浣撹В鏋愭湇鍔?
    
    Args:
        llm_func: LLM鍑芥暟锛屾帴鏀秔rompt杩斿洖鍥炵瓟
        embed_func: 宓屽叆鍚戦噺鐢熸垚鍑芥暟锛屾帴鏀舵枃鏈繑鍥炲祵鍏ュ悜閲?
        similarity_threshold: 鍚戦噺鐩镐技搴﹂槇鍊?
        top_k: 杩斿洖鍓岾涓€欓€?
        use_threshold: 鏄惁浣跨敤闃堝€兼ā寮?
        data_path: 瀹炰綋搴撴暟鎹枃浠惰矾寰勶紝濡傛灉鎻愪緵鍒欎粠璇ヨ矾寰勫姞杞芥暟鎹?
        
    Returns:
        EntityResolutionService 瀹炰緥
    """
    service = EntityResolutionService(
        llm_func=llm_func,
        embed_func=embed_func,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        use_threshold=use_threshold,
        data_path=data_path
    )
    
    return service
