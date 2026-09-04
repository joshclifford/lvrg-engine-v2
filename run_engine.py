"""
LVRG Lead Magnet Engine — Main Runner
Usage:
  python run_engine.py barkamon.com
  python run_engine.py barkamon.com toastcatering.com hiddencraftsd.com
  python run_engine.py --file prospects.txt
  python run_engine.py --campaign "SD Food & Bev Q2" barkamon.com

Options:
  --file FILE         Read domains from a text file (one per line)
  --campaign NAME     Instantly campaign name (default: LVRG LM2 - Website Rebuild)
  --no-instantly      Skip Instantly push (site + email only)
  --no-deploy         Skip GitHub deploy (local files only)
  --icp TYPE          ICP type hint for Claude (e.g. "restaurant", "catering")
  --city CITY         City context (default: San Diego, CA)
  --offer OFFER       Offer type: Website Rebuild | Website Grade | Smart Site | AI Chat
  --cta CTA           CTA: Book a Call | Claim Your Site | Get Your Grade | Watch Demo
"""

import sys
import os
import json
import argparse
import time
from datetime import datetime

# Engine modules
from slug import make_slug
from intel import scrape_site, grade_site
from generator import generate_site, generate_email
from deploy import deploy_site
from instantly import get_or_create_campaign, add_lead
from supabase_client import upsert_lead, log_event, update_lead_status

# Set Firecrawl key from environment or hardcode for now
os.environ["FIRECRAWL_API_KEY"] = os.environ.get("FIRECRAWL_API_KEY", "")


def process_prospect(domain: str, campaign_id: str, args) -> dict:
    """Run the full pipeline for a single prospect domain."""
    
    domain = domain.strip().lower()
    if not domain:
        return None
    
    # Generate a clean ID — shared with api.py so the CLI and the API can never
    # produce different slugs for the same domain again.
    prospect_id = make_slug(domain)
    
    print(f"\n{'='*60}")
    print(f"PROCESSING: {domain}")
    print(f"ID: {prospect_id}")
    print(f"{'='*60}")
    
    result = {
        "domain": domain,
        "prospect_id": prospect_id,
        "timestamp": datetime.now().isoformat(),
        "status": "started",
        "intel": None,
        "grade": None,
        "site_dir": None,
        "preview_url": None,
        "email": None,
        "instantly_pushed": False,
    }
    
    try:
        # Step 1: Intel gather
        print(f"\n[1/5] Gathering intel...")
        intel = scrape_site(domain)
        result["intel"] = intel
        
        # Step 2: Grade site
        print(f"\n[2/5] Grading site...")
        grade = grade_site(intel)
        result["grade"] = grade
        print(f"  Score: {grade['total']}/10 — {grade['verdict']}")
        
        if not grade["worth_targeting"]:
            print(f"  ⚠️  Site score {grade['total']} outside target range (2-7). Skipping.")
            result["status"] = "skipped_grade"
            return result
        
        # Inject ICP context if provided
        if args.icp:
            intel["business_type"] = args.icp
        if args.city:
            intel["location"] = intel.get("location") or args.city
        
        # Step 3: Generate site
        print(f"\n[3/5] Generating preview site...")
        # Same lift as api.py: the bytes are the generator's business, not the
        # intel record's. See the note there.
        site_dir = generate_site(intel, prospect_id,
                                 photo_assets=intel.pop("photo_assets", None))
        result["site_dir"] = site_dir
        
        # Step 4: Deploy
        if not args.no_deploy:
            print(f"\n[4/5] Deploying to GitHub Pages...")
            preview_url = deploy_site(prospect_id, site_dir)
            result["preview_url"] = preview_url
        else:
            result["preview_url"] = f"[local only] {site_dir}/index.html"
            print(f"\n[4/5] Deploy skipped (--no-deploy)")
        
        # Step 5: Generate email
        print(f"\n[5/5] Drafting outreach email...")
        email_data = generate_email(intel, grade, prospect_id)
        result["email"] = email_data
        
        # Step 6: Push to Instantly
        instantly_lead_id = None
        if not args.no_instantly and campaign_id:
            print(f"\n[+] Pushing to Instantly...")
            pushed = add_lead(campaign_id, intel, email_data)
            result["instantly_pushed"] = pushed
            if pushed and isinstance(pushed, dict):
                instantly_lead_id = pushed.get("id")
        
        # Step 7: Save to Supabase
        print(f"\n[+] Saving to Supabase...")
        lead_status = "sent" if result.get("instantly_pushed") else "built"
        sb_lead = upsert_lead(
            domain=domain,
            intel=intel,
            grade=grade,
            preview_url=result.get("preview_url"),
            email_data=email_data,
            instantly_lead_id=instantly_lead_id,
            instantly_campaign_id=campaign_id,
            offer=getattr(args, 'offer', 'Website Rebuild'),
            cta=getattr(args, 'cta', 'Book a Call'),
            status=lead_status,
        )
        if sb_lead:
            result["supabase_id"] = sb_lead.get("id")
            log_event(sb_lead["id"], "site_built", {"preview_url": result.get("preview_url"), "score": grade.get("total")})
            if lead_status == "sent":
                log_event(sb_lead["id"], "sent", {"campaign_id": campaign_id})
        
        result["status"] = "complete"
        
        # Print summary
        print(f"\n{'─'*60}")
        print(f"✅ COMPLETE: {intel['business_name']}")
        print(f"   Site score: {grade['total']}/10")
        print(f"   Preview URL: {result['preview_url']}")
        print(f"   Subject (recommended): {email_data.get('subject_' + email_data.get('recommended_subject', 'b'), '')}")
        print(f"{'─'*60}")
        
    except Exception as e:
        print(f"\n❌ ERROR processing {domain}: {e}")
        import traceback
        traceback.print_exc()
        result["status"] = f"error: {str(e)}"
    
    return result


def main():
    parser = argparse.ArgumentParser(description="LVRG Lead Magnet Engine")
    parser.add_argument("domains", nargs="*", help="Domain(s) to process")
    parser.add_argument("--file", "-f", help="File with one domain per line")
    parser.add_argument("--campaign", "-c", default="LVRG LM2 - Website Rebuild", help="Instantly campaign name")
    parser.add_argument("--no-instantly", action="store_true", help="Skip Instantly push")
    parser.add_argument("--no-deploy", action="store_true", help="Skip GitHub Pages deploy")
    parser.add_argument("--icp", help="ICP type hint (restaurant, catering, bar, etc.)")
    parser.add_argument("--city", default="San Diego, CA", help="City context")
    parser.add_argument("--offer", default="Website Rebuild", choices=["Website Rebuild", "Website Grade", "Smart Site", "AI Chat"], help="Offer type for this run")
    parser.add_argument("--cta", default="Book a Call", choices=["Book a Call", "Claim Your Site", "Get Your Grade", "Watch Demo"], help="CTA for this run")
    args = parser.parse_args()
    
    # Collect domains
    domains = list(args.domains)
    if args.file:
        with open(args.file) as f:
            domains += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    if not domains:
        print("No domains provided. Usage: python run_engine.py domain.com")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print(f"LVRG LEAD MAGNET ENGINE — V1")
    print(f"Prospects: {len(domains)}")
    print(f"Campaign: {args.campaign}")
    print(f"Offer: {args.offer}")
    print(f"CTA: {args.cta}")
    print(f"Deploy: {'No' if args.no_deploy else 'GitHub Pages'}")
    print(f"Instantly: {'No' if args.no_instantly else 'Yes'}")
    print(f"{'='*60}")
    
    # Get/create Instantly campaign
    campaign_id = None
    if not args.no_instantly:
        print(f"\nConnecting to Instantly...")
        campaign_id = get_or_create_campaign(args.campaign)
        if not campaign_id:
            print("Warning: Could not connect to Instantly. Continuing without it.")
    
    # Process all prospects
    results = []
    for domain in domains:
        result = process_prospect(domain, campaign_id, args)
        if result:
            results.append(result)
        time.sleep(2)  # Be polite to APIs
    
    # Final summary
    complete = [r for r in results if r["status"] == "complete"]
    skipped = [r for r in results if "skip" in r.get("status", "")]
    errors = [r for r in results if "error" in r.get("status", "")]
    
    print(f"\n{'='*60}")
    print(f"ENGINE RUN COMPLETE")
    print(f"  ✅ Processed: {len(complete)}")
    print(f"  ⏭️  Skipped:   {len(skipped)}")
    print(f"  ❌ Errors:    {len(errors)}")
    print(f"\nPreview URLs:")
    for r in complete:
        print(f"  {r['domain']} → {r['preview_url']}")
    
    # Save run log
    log_path = f"/home/user/workspace/lvrg-engine/output/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nRun log saved: {log_path}")
    print(f"{'='*60}")
    
    return results


if __name__ == "__main__":
    main()
