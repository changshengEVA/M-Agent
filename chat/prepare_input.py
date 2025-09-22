## 0922 changshengEVA: 新增一个prepare_input类别整合chat场景下的模型的输入信息
## >> TODO >> 将纯文本的对话信息转化为符合指令训练的结构信息
## >> TODO >> 分为数据收集模式
import yaml
import os
from llama_index.core.indices.base import BaseIndex

# 0922: 初始化prompt与语言信息
with open("./prompt.yaml","r",encoding="utf-8") as file:
    prompt_data = yaml.safe_load(file)
SELFWOM,SELFWOM_WO_OB,OBSERVATION,HISTORY,MEMORY,FIND  =   prompt_data["obserchatprompt"]["selfwom"],\
                                prompt_data["obserchatprompt"]["selfwom_wo_ob"],\
                                prompt_data["obserchatprompt"]["observation"],\
                                prompt_data["obserchatprompt"]["history"],\
                                prompt_data["obserchatprompt"]["memory"],\
                                prompt_data['obserchatprompt']['findmemory']
# print(SELFWOM,OBSERVATION,HISTORY)
LANGUAGE = os.getenv("LANGUAGE")

# 0922: 查找记忆库的初等方式
def find_memory(index: BaseIndex, find: str):
    query_engine = index.as_query_engine()
    response = query_engine.query(FIND.format(find))
    return response

# 0922: 产生正常对话场景的非记忆性对话
def emerge_chat_prompt_wo_memory(history,observation = None): 
    if observation:
        return f"{SELFWOM.format(LANGUAGE)}{OBSERVATION.format(observation)}{HISTORY.format(history)}"
    else:
        return f"{SELFWOM_WO_OB.format(LANGUAGE)}{HISTORY.format(history)}"
    
# 0922：产生正常对话场景的记忆性对话
def emerge_chat_prompt_w_memory(history,memory,observation = None): 
    if observation: 
        return f"{SELFWOM.format(LANGUAGE)}{MEMORY.format(memory)}{OBSERVATION.format(observation)}{HISTORY.format(history)}"
    else: 
        return f"{SELFWOM_WO_OB.format(LANGUAGE)}{MEMORY.format(memory)}{HISTORY.format(history)}"
