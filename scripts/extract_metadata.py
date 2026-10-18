"""
@module scripts.extract_metadata
@purpose Extract semantic metadata from python files to build agent index.
@owns ast parsing, metadata JSON generation
@does_not_own orchestration, UI, database interactions
@key_exports build_repo_metadata
"""

import ast
import yaml
import os
import sys
from pathlib import Path
from typing import Dict, Any, List

def parse_docstring(doc: str) -> Dict[str, Any]:
    """
    @summary Parses a standard docstring containing @tags into a structured dict.
    @inputs doc: raw docstring
    @outputs dictionary of parsed tags to string/list values
    @side_effects none
    """
    if not doc:
        return {}
    
    parsed = {}
    lines = doc.split('\n')
    current_tag = None
    current_value = []
    
    for line in lines:
        line = line.strip()
        if line.startswith('@'):
            if current_tag:
                parsed[current_tag] = '\n'.join(current_value).strip()
            
            parts = line.split(' ', 1)
            current_tag = parts[0][1:]
            current_value = [parts[1]] if len(parts) > 1 else []
        elif current_tag:
            current_value.append(line)
            
    if current_tag:
        parsed[current_tag] = '\n'.join(current_value).strip()
        
    return parsed

def extract_file_metadata(filepath: Path) -> Dict[str, Any]:
    """
    @summary Extract module and symbol metadata from a single Python file using AST.
    @inputs filepath: Path object to the .py file
    @outputs dict containing module metadata and list of symbol metadata
    @side_effects reads file from disk
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return {}

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        print(f"Syntax error in {filepath}: {e}")
        return {}

    module_doc = ast.get_docstring(tree)
    module_data = parse_docstring(module_doc)
    module_data['path'] = str(filepath)
    
    symbols = []
    
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node)
            if doc:
                symbol_data = parse_docstring(doc)
                symbol_data['symbol'] = node.name
                symbol_data['kind'] = 'class' if isinstance(node, ast.ClassDef) else 'function'
                symbol_data['path'] = str(filepath)
                symbols.append(symbol_data)
                
    return {
        'module': module_data,
        'symbols': symbols
    }

def build_repo_metadata(search_dir: str, out_dir: str):
    """
    @summary Walk the directory, extract all metadata, and write JSON lines to output directory.
    @inputs search_dir: root directory to search, out_dir: output directory for JSON metadata
    @outputs none
    @side_effects writes symbols.jsonl and modules.jsonl to out_dir
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    modules_out = out_path / 'modules.yaml'
    symbols_out = out_path / 'symbols.yaml'
    
    all_modules = []
    all_symbols = []
    
    for root, dirs, files in os.walk(search_dir):
        # Skip hidden dirs and node_modules
        dirs_to_remove = [d for d in dirs if d.startswith('.') or d in ('node_modules', 'venv')]
        for d in dirs_to_remove:
            dirs.remove(d)
        
        for file in files:
            if file.endswith('.py'):
                filepath = Path(root) / file
                data = extract_file_metadata(filepath)
                
                if data.get('module'):
                    all_modules.append(data['module'])
                    
                all_symbols.extend(data.get('symbols', []))

    with open(modules_out, 'w') as mf:
        yaml.dump(all_modules, mf, sort_keys=False)
        
    with open(symbols_out, 'w') as sf:
        yaml.dump(all_symbols, sf, sort_keys=False)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python extract_metadata.py <source_dir> <out_dir>")
        sys.exit(1)
        
    build_repo_metadata(sys.argv[1], sys.argv[2])
