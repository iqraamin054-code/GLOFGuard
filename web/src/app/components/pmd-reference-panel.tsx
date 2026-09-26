/** Historical source metadata only; never part of the live monitoring rows. */
export default function PmdReferencePanel() {
  return (
    <section
      aria-labelledby="pmd-reference-heading"
      className="mb-8 rounded-2xl border border-slate-700 bg-slate-900 p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
          Historical reference source
        </p>
        <span className="rounded-full border border-sky-400/30 bg-sky-400/10 px-3 py-1 text-xs font-medium text-sky-200">
          PMD Official Inventory · 2013
        </span>
      </div>
      <h2 id="pmd-reference-heading" className="mt-3 text-lg font-semibold text-white">
        Pakistan Meteorological Department (PMD) — Glacial Lakes Inventory 2013
      </h2>
      <p className="mt-2 text-sm font-medium text-amber-200">
        Historical reference inventory — not a live warning feed.
      </p>
      <p className="mt-3 max-w-4xl text-sm leading-6 text-slate-300">
        This separate historical inventory does not replace the current GLOF Guard
        inventory or its Sentinel-2, GSMaP, GFS, and NASA POWER measurements.
        Spatial links require a reviewed crosswalk; PMD lake IDs are not PKGL IDs.
      </p>
      <p className="mt-3 text-xs text-slate-400">
        Local reference · review pending. Not imported to Supabase or applied to
        the monitoring map.
      </p>
    </section>
  );
}
