# Course LoRA offline eval (Step 14)

- seed: `14`
- n: `40` (fixed test split sample)
- adapter: `training/llamafactory/outputs/qwen3_8b_lora_course`

| 指标 | Base | LoRA | 变化 |
|---|---:|---:|---:|
| JSON parse rate | 0.0000 | 0.8750 | 0.8750 |
| Schema validity | 0.0000 | 0.8750 | 0.8750 |
| Required field coverage | 0.0000 | 0.7250 | 0.7250 |
| Verdict accuracy | 0.0000 | 0.6667 | 0.6667 |
| Field-level accuracy | 0.0000 | 0.4500 | 0.4500 |
| Evidence support | 0.0000 | 0.0000 | 0.0000 |
| Citation validity | 0.0000 | 0.0000 | 0.0000 |
| Average latency (ms) | 5553.7550 | 3115.7700 | -2437.9850 |
| Failed cases | 40.0000 | 5.0000 | -35.0000 |

## Honest conclusion

LoRA improves both structure and some field-level exact match on this sample; still course_pilot (not human_gold).

## Spot checks (first 5 paired rows)

### Sample 1 task=risk_detect
- base tags: ['format_error']
- lora tags: ['field_value_error']
- base preview: `<think> 好的，我现在需要处理用户关于分支机构投标时的合规风险识别请求。首先，用户提到的条件是分支机构投标时必须提供总公司和分公司的营业执照副本复印件，以及总公司出具的授权书。我需要仔细分析这些要求可能涉及的合规风险点。  首先，营业执照副本复印件是否有效？用户可能担心复印件是否被接受，或者是否需要加盖公章。如果复印件没有公章，可能会被视为无效，导致投标被拒。另外，复印件是否需要与原件核对，`
- lora preview: `<think>  </think>  {"risk_level":"high","risk_type":"other","reason":"分支机构投标的，须提供总公司和分公司营业执照副本复印件，总公司出具给分支机构的授权书。","is_rejection_clause":false}`

### Sample 2 task=risk_detect
- base tags: ['format_error']
- lora tags: []
- base preview: `<think> 好的，我现在需要处理用户提供的这个招投标合规风险识别请求。首先，用户给出的条款是：“卖方应保证所交付的货物为交货日期前6 个月内生产的全新产品，若交”。看起来用户可能是在检查招标文件中的某个条款，需要识别其中的合规风险。  首先，我要仔细分析这个条款的内容。条款要求卖方交付的货物必须是在交货日期前6个月内生产的全新产品。这里有几个关键点需要注意：时间范围、产品状态（全新）、以及可能`
- lora preview: `<think>  </think>  {"risk_level":"high","risk_type":"other","reason":"（2）卖方应保证所交付的货物为交货日期前6 个月内生产的全新产品，若交","is_rejection_clause":false}`

### Sample 3 task=requirement_classify
- base tags: ['format_error']
- lora tags: ['field_value_error']
- base preview: `<think> 好的，我现在需要分析用户提供的这个条款：“（3）采购需求中标注‘▲’号条款为重要技术参数，但不作为无效响应条款。” 首先，我要确定这个条款的类别和是否强制。  首先，条款的类别。用户提到的条款涉及采购需求中的技术参数，特别是标注了“▲”号的部分。根据常见的招投标文件分类，技术参数通常属于技术条款，而采购需求本身可能属于采购需求条款。但这里更具体的是关于技术参数的标注，所以可能属于技`
- lora preview: `<think>  </think>  {"category":"technical","mandatory":false,"risk_level":"medium","confidence":0.55}`

### Sample 4 task=qualification_extract
- base tags: ['format_error']
- lora tags: ['field_value_error']
- base preview: `<think> 好的，我现在需要处理用户提供的这个资格要求：“产品类别按《中国强制性产品认证目录》的相关规定）的复印件；”。首先，我要仔细分析这句话的结构和内容，确保正确理解用户的需求。  首先，用户提到的是“产品类别按《中国强制性产品认证目录》的相关规定”，这可能意味着他们需要供应商提供的产品类别符合该目录的要求。接下来是“复印件”，说明需要的是文件的复印件，而不是原件。但这里可能存在一些语法问`
- lora preview: `<think>  </think>  {"requirements":["产品类别按《中国强制性产品认证目录》的相关规定）的复印件；"],"mandatory":true,"evidence_required":["资质证书"]}`

### Sample 5 task=citation_qa
- base tags: ['format_error']
- lora tags: ['field_value_error']
- base preview: `<think> 好的，用户问的是本项目对投标人的财务状况有什么要求，而且特别指出要引用文件第2页。首先，我需要确认用户提到的文件第2页内容。根据之前提供的资料，文件第2页确实提到了财务状况的要求。接下来，我需要仔细查看该页的内容，找出具体的财务要求。  文件第2页提到投标人需提供近三年的财务报表，包括资产负债表、利润表和现金流量表，并且这些报表需要由具有资质的会计师事务所审计。此外，还要求投标人提`
- lora preview: `<think>  </think>  {"answer":"本项目对投标人的财务状况要求如下：","citations":["31c60990-312c-594c-892c-312c594c892c"],"answerable":true}`

