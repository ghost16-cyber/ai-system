import { Download, FileText, RefreshCw, Save } from "lucide-react";
import { useMemo, useState } from "react";

import { HttpAstraClient, type GroundedAssignmentReport, type ReportExportReadiness } from "../clients/astraClient";
import { reportSectionMatches, reportStateLabel, toggleSubmissionFile, type ReportSectionFilter } from "../state/assignmentReportState";

interface Props { client: HttpAstraClient; assignmentId: string | null; workspacePath: string }

export function AssignmentReportSection({ client, assignmentId, workspacePath }: Props) {
  const [report, setReport] = useState<GroundedAssignmentReport | null>(null);
  const [readiness, setReadiness] = useState<ReportExportReadiness | null>(null);
  const [filter, setFilter] = useState<ReportSectionFilter>("all");
  const [reportTitle, setReportTitle] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [exports, setExports] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const visible = useMemo(() => (report?.report_sections ?? []).filter((section) => reportSectionMatches(section, filter)), [filter, report]);

  async function refreshState(next: GroundedAssignmentReport) {
    if (!assignmentId) return;
    setReport(next);
    setReportTitle(next.title);
    setReadiness(await client.getAssignmentReportReadiness(assignmentId, next.report_id, workspacePath));
    setExports((await client.getAssignmentReportExports(assignmentId, next.report_id, workspacePath)).exports);
  }

  async function create() {
    if (!assignmentId) return;
    setBusy(true); setError(null);
    try { await refreshState(await client.createAssignmentReport(assignmentId, { workspace_path: workspacePath })); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Report creation failed."); }
    finally { setBusy(false); }
  }

  async function save(sectionId: string) {
    if (!assignmentId || !report) return;
    setBusy(true); setError(null);
    try {
      await refreshState(await client.updateAssignmentReport(assignmentId, report.report_id, { workspace_path: workspacePath, changes: { sections: [{ section_id: sectionId, user_editable_notes: notes[sectionId] ?? "" }] } }));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Report revision failed."); }
    finally { setBusy(false); }
  }

  async function revise(changes: Record<string, unknown>) {
    if (!assignmentId || !report) return;
    setBusy(true); setError(null);
    try { await refreshState(await client.updateAssignmentReport(assignmentId, report.report_id, { workspace_path: workspacePath, changes })); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Report revision failed."); }
    finally { setBusy(false); }
  }

  function moveSection(sectionId: string, direction: -1 | 1) {
    if (!report) return;
    const order = report.report_sections.map((section) => section.section_id);
    const index = order.indexOf(sectionId); const target = index + direction;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    void revise({ section_order: order });
  }

  async function exportReport(format: string) {
    if (!assignmentId || !report) return;
    setBusy(true); setError(null);
    try {
      await client.exportAssignmentReportV2(assignmentId, report.report_id, { workspace_path: workspacePath, format, selected_files: format === "zip" ? selectedFiles : [] });
      setExports((await client.getAssignmentReportExports(assignmentId, report.report_id, workspacePath)).exports);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Report export failed."); }
    finally { setBusy(false); }
  }

  return (
    <section className="panel assignment-report">
      <div className="panel-title-row">
        <div className="panel-title"><FileText size={18} /><h2>Assignment Report</h2></div>
        <button className="secondary-button" onClick={() => void create()} disabled={busy || !assignmentId || !workspacePath.trim()}>{busy ? <RefreshCw className="spin" size={16} /> : <FileText size={16} />} Create grounded report</button>
      </div>
      <div className="notice subtle">Astra-generated text is requirement- and evidence-grounded. Placeholders and user-authored notes remain visibly distinct; no submission or evidence acceptance is automatic.</div>
      {error && <div className="notice amber">{error}</div>}
      {report && readiness && <>
        <div className="report-title-editor"><label>Report title<input value={reportTitle} onChange={(event) => setReportTitle(event.target.value)} /></label><button className="secondary-button" onClick={() => void revise({ title: reportTitle })} disabled={busy}><Save size={15} /> Save title</button></div>
        <div className="readiness-metrics">
          <div><strong>{readiness.traceability_coverage_percentage}%</strong><span>Traceability</span></div><div><strong>{readiness.supported_section_count}</strong><span>Supported</span></div><div><strong>{readiness.unresolved_placeholder_count}</strong><span>Placeholders</span></div><div><strong>{readiness.stale_evidence_count}</strong><span>Stale</span></div><div><strong>{readiness.failed_evidence_count}</strong><span>Failed</span></div><div><strong>{readiness.manual_review_count}</strong><span>Manual review</span></div>
        </div>
        <div className="notice amber">Export readiness: {readiness.status.replace(/_/g, " ")}. {readiness.export_blockers.join(" ")}</div>
        <div className="filter-row">{(["all", "supported", "placeholders", "stale", "failed"] as ReportSectionFilter[]).map((item) => <button key={item} className={filter === item ? "filter-button active" : "filter-button"} onClick={() => setFilter(item)}>{item}</button>)}</div>
        <div className="report-layout">
          <nav className="report-nav">{report.report_sections.map((section) => <a key={section.section_id} href={`#report-${section.section_id}`}>{section.title}<span>{reportStateLabel(section.verification_state)}</span></a>)}</nav>
          <div className="report-section-list">{visible.map((section) => <article id={`report-${section.section_id}`} className="requirement-card" key={section.section_id}>
            <div className="execution-plan-header"><strong>{section.title}</strong><span className={`status-pill requirement-${section.verification_state}`}>{reportStateLabel(section.verification_state)}</span></div>
            <span>{section.purpose}</span>
            {section.grounded_content_blocks.map((block) => <div className={`report-block ${block.block_type}`} key={block.block_id}><strong>{block.user_authored ? "User-authored" : block.block_type.replace(/_/g, " ")}</strong><p>{block.text}</p>{block.evidence_references.some((reference) => section.selected_evidence.includes(reference)) && <code>{block.evidence_references.filter((reference) => section.selected_evidence.includes(reference)).join(", ")}</code>}</div>)}
            {section.linked_evidence.length > 0 && <div className="selection-list"><strong>Supporting evidence selection</strong>{section.linked_evidence.map((reference) => <label key={reference}><input type="checkbox" checked={section.selected_evidence.includes(reference)} onChange={() => void revise({ sections: [{ section_id: section.section_id, selected_evidence: section.selected_evidence.includes(reference) ? section.selected_evidence.filter((item) => item !== reference) : [...section.selected_evidence, reference] }] })} /> {reference}</label>)}</div>}
            {section.warnings.map((warning) => <div className="notice amber" key={warning}>{warning}</div>)}
            <div className="button-row"><button className="secondary-button" onClick={() => moveSection(section.section_id, -1)} disabled={busy}>Move up</button><button className="secondary-button" onClick={() => moveSection(section.section_id, 1)} disabled={busy}>Move down</button>{!section.mandatory && <button className="secondary-button" onClick={() => void revise({ sections: [{ section_id: section.section_id, inclusion_status: section.inclusion_status === "included" ? "excluded" : "included" }] })} disabled={busy}>{section.inclusion_status === "included" ? "Exclude optional section" : "Include optional section"}</button>}</div>
            <label>User-authored notes<textarea value={notes[section.section_id] ?? section.user_editable_notes} onChange={(event) => setNotes((current) => ({ ...current, [section.section_id]: event.target.value }))} /></label>
            <button className="secondary-button" onClick={() => void save(section.section_id)} disabled={busy}><Save size={15} /> Save revision</button>
          </article>)}</div>
        </div>
        <section><h3 className="section-subtitle">Submission files — explicit selection only</h3><div className="selection-list">{report.recommended_submission_files.map((path) => <label key={path}><input type="checkbox" checked={selectedFiles.includes(path)} onChange={() => setSelectedFiles((current) => toggleSubmissionFile(current, path))} /> {path}</label>)}</div></section>
        <div className="button-row">{["markdown", "json", "docx", "zip"].map((format) => <button className="secondary-button" key={format} onClick={() => void exportReport(format)} disabled={busy}><Download size={15} /> Export {format.toUpperCase()}</button>)}</div>
        <section><h3 className="section-subtitle">Export history</h3><div className="compact-list">{exports.map((item) => <div key={String(item.export_id)}><a href={client.assignmentReportExportUrl(assignmentId!, report.report_id, String(item.export_id), workspacePath)}><strong>{String(item.filename)}</strong></a><span>{String(item.format)} · {String(item.created_at)}</span></div>)}</div></section>
        <span className="helper-text">Revision {report.current_revision_id} · {report.revisions.length} revision(s)</span>
      </>}
      {!report && <div className="empty-inline">Create a report after deterministic evidence verification. No files are selected or exported automatically.</div>}
    </section>
  );
}
