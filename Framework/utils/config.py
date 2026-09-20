# maps the adapter names to their respective module paths, container names, and workspace paths
# NOTE TO DO: apply these in adapter class definitions and Dockerfiles; search for and clean up other hardcoded paths

attack_version = "19.2" # set to None to enable update

# map of adapter names to their module paths
adapter_module_map = {
    'AttacKG': 'Framework.adapters.attackg_adapter.AttacKGAdapter',
    'Buchel': 'Framework.adapters.buchel_adapter.BuchelAdapter',
    'Orbinato': 'Framework.adapters.orbinato_adapter.OrbinatoAdapter',
    'RAF-AG': 'Framework.adapters.raf_ag_adapter.RAFAGAdapter',
    'rcATT': 'Framework.adapters.rcatt_adapter.RcATTAdapter',
    'TRAM': 'Framework.adapters.tram_adapter.TRAMAdapter',
    'TTP-LLM': 'Framework.adapters.ttp_llm_adapter.TTPLLMAdapter',
    'TTPDrill': 'Framework.adapters.ttpdrill_adapter.TTPDrillAdapter',
    'LADDER': 'Framework.adapters.ladder_adapter.LADDERAdapter',
    'SeqMask': 'Framework.adapters.seqmask_adapter.SeqMaskAdapter'
}

# map of adapter names to their Docker image names
adapter_container_map = {
    'AttacKG': 'ttp-workbench:attackg',
    'Buchel': 'localhost/generation_app:latest',
    'Orbinato': 'ttp-workbench:orbinato',
    'RAF-AG': 'ttp-workbench:raf-ag',
    'rcATT': 'ttp-workbench:rcatt',
    'TRAM': 'ttp-workbench:tram',
    'TTP-LLM': 'ttp-workbench:ttp-llm',
    'TTPDrill': 'ttp-workbench:ttpdrill',
    'LADDER': 'ttp-workbench:ladder',
    'SeqMask': 'ttp-workbench:seqmask'
}

# map of adapter names to their workspace paths inside the container (we use this to copy data in the curation scripts)
adapter_workspace_map = {
    'AttacKG': '/opt/AttacKG',
    'Buchel': '/workspace',
    'Orbinato': '/opt/Orbinato',
    'RAF-AG': '/opt/RAF-AG',
    'rcATT': '/opt/rcATT',
    'TRAM': '/opt/TRAM',
    'TTP-LLM': '/opt/TTP-LLM',
    'TTPDrill': '/opt/TTPDrill',
    'LADDER': '/opt/LADDER',
    'SeqMask': '/opt/SeqMask'
}

adapter_repo_map = {
    'AttacKG': {'repo_url':'https://github.com/li-zhenyuan/Knowledge-enhanced-Attack-Graph.git',
                'commit_id':'9120ebea25383bfca1254d2b3088266b3680e47b'},
    'Buchel': None,
    'Orbinato': {'repo_url':'https://github.com/dessertlab/cti-to-mitre-with-nlp.git',
                'commit_id':'a8cacf3185d098c686e0d88768a619a03a4d76d1'},
    'RAF-AG': {'repo_url':'https://github.com/cyb3rlab/RAF-AG.git',
                'commit_id':'f2868edc1be6a09fc51b1d57907799602ddaa0eb'},
    'rcATT': {'repo_url':'https://github.com/vlegoy/rcATT.git',
                'commit_id':'f82f7fd456279abefcd3e0b50e8056345c11aeb7'},
    'TRAM': {'repo_url':'https://github.com/center-for-threat-informed-defense/tram.git',
                'commit_id':'f29793d8d665f7f552898696e00065ef24a29a20'},
    'TTP-LLM': {'repo_url':'https://github.com/RezzFayyazi/TTP-LLM.git',
                'commit_id':'7b8ce19aa608769e86ecf7bddb6dc089e023ffa9'},
    'TTPDrill': [
        {'repo_url':'https://github.com/ccsnow127/TTPDrill-1.0.git',
         'commit_id':'48c99ae855e625ad9b9cdc71e7f6a597db898c99'},
        {'repo_url':'https://github.com/SkyBulk/TTPDrill-0.3.git',
         'commit_id':'78435268f26e71c966af53321a4a6037a6bb7853'},
        {'repo_url':'https://github.com/KaiLiu-Leo/TTPDrill-0.5.git',
         'commit_id':'75bb4362ce80492af8f25da0fedeb4dc107edd5c'}],
}
