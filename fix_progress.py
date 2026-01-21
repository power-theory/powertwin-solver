#!/usr/bin/env python3
"""
Quick script to fix progress tracking in run_UOsim.py
Changes progress update from every 10 assets to every 1 asset
"""

import os

# File to fix
file_path = "solver/app/modules/simulation/run_UOsim.py"

# Read the file
with open(file_path, 'r') as f:
    content = f.read()

# Old code block to replace
old_code = """        assets_processed_batch += 1
        
        # Update progress every 10 assets to reduce I/O overhead
        if assets_processed_batch % 10 == 0:
            from app.views import save_simulation_state, get_current_simulation
            current_sim = get_current_simulation()
            if current_sim:
                current_progress = current_sim.get('progress', {})
                # Increment the global counter
                new_assets_processed = current_progress.get('assets_processed', 0) + 10
                current_progress['assets_processed'] = new_assets_processed
                current_progress['current_step'] = f'processing_batch_{batch_num}'
                save_simulation_state(simulation_name, 'running', current_progress)
                logger.debug(f"BATCH {batch_num}: Updated progress - {new_assets_processed} assets processed")"""

# New code block
new_code = """        assets_processed_batch += 1
        
        # Update progress after every asset is processed
        from app.views import save_simulation_state, get_current_simulation
        current_sim = get_current_simulation()
        if current_sim:
            current_progress = current_sim.get('progress', {})
            # Increment the global counter by 1 for each asset
            new_assets_processed = current_progress.get('assets_processed', 0) + 1
            current_progress['assets_processed'] = new_assets_processed
            current_progress['current_step'] = f'processing_batch_{batch_num}'
            save_simulation_state(simulation_name, 'running', current_progress)
            logger.debug(f"BATCH {batch_num}: Updated progress - {new_assets_processed} assets processed")"""

# Replace
if old_code in content:
    content = content.replace(old_code, new_code)
    print("✓ Found and replaced progress tracking code")
else:
    print("✗ Could not find old code block")
    print("Searching for partial match...")
    if "# Update progress every 10 assets" in content:
        print("Found partial match - the code structure may have changed")

# Write back
with open(file_path, 'w') as f:
    f.write(content)

print(f"✓ Updated {file_path}")

# Also need to fix views.py to ensure total_assets is fetched from database
views_file = "solver/app/views.py"

with open(views_file, 'r') as f:
    views_content = f.read()

# Check if the fix is already there
if "Ensure total_assets is set from database" not in views_content:
    print("\n⚠ Also need to update views.py to fetch total_assets from database")
    print("This will ensure total_assets shows correct value instead of 0 or 1")
    old_views = """        simulation_name = current_sim.get('simulation_name')
        progress = current_sim.get('progress', {})
        
        # Query database as fallback for assets_processed count
        try:
            from modules.diagnostics import get_asset_total"""
    
    new_views = """        simulation_name = current_sim.get('simulation_name')
        progress = current_sim.get('progress', {})
        
        # Ensure total_assets is set from database if not in state file
        if progress.get('total_assets') is None or progress.get('total_assets') == 0:
            try:
                from modules.diagnostics import get_asset_total
                total_in_db = get_asset_total(simulation_name)
                progress['total_assets'] = total_in_db
                logger.debug(f"Updated total_assets from database: {total_in_db}")
            except Exception as e:
                logger.debug(f"Could not get total_assets from database: {str(e)}")
        
        # Query database as fallback for assets_processed count
        try:
            from modules.diagnostics import get_asset_total"""
    
    if old_views in views_content:
        views_content = views_content.replace(old_views, new_views)
        with open(views_file, 'w') as f:
            f.write(views_content)
        print(f"✓ Updated {views_file} to fetch total_assets from database")
    else:
        print(f"Could not auto-update {views_file} - check structure")

print("\n✓ Done! Restart the app and progress should update per asset.")
