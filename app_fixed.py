import React, { useMemo, useState } from "react";
// No external UI libs required; works in a fresh Vite + React app.
// Optional: npm i papaparse --save  (we include a tiny CSV parser here to avoid deps)

// Minimal CSV parser (handles commas, quotes, headers). For robust parsing, use PapaParse.
function parseCSV(text) {
  const rows = [];
  let cur = '', inQuotes = false, row = [];
  for (let i = 0; i < text.length; i++) {
    const c = text[i], n = text[i + 1];
    if (c === '"') {
      if (inQuotes && n === '"') { cur += '"'; i++; }
      else { inQuotes = !inQuotes; }
    } else if (c === ',' && !inQuotes) { row.push(cur); cur = ''; }
    else if ((c === '\n' || c === '\r') && !inQuotes) {
      if (cur !== '' || row.length) { row.push(cur); rows.push(row); row = []; cur = ''; }
      if (c === '\r' && n === '\n') i++;
    } else { cur += c; }
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row); }
  // Convert to objects by header
  const [header, ...body] = rows;
  return body.map(r => Object.fromEntries(header.map((h, idx) => [h.trim(), (r[idx] ?? '').trim()])));
}

export default function AvailabilityForm() {
  const [formQuestionsFile, setFormQuestionsFile] = useState(null);
  const [servingBaseFile, setServingBaseFile] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [mapping, setMapping] = useState([]); // rows with { Director, "Serving Girl" }

  const [answers, setAnswers] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [errors, setErrors] = useState({});

  function readFile(file, onLoad) {
    const reader = new FileReader();
    reader.onload = e => onLoad(e.target.result);
    reader.readAsText(file);
  }

  function handleLoadFormQuestions(file) {
    setFormQuestionsFile(file);
    readFile(file, text => {
      try { setQuestions(parseCSV(text)); }
      catch (e) { console.error(e); alert('Could not parse Form questions.csv'); }
    });
  }

  function handleLoadServingBase(file) {
    setServingBaseFile(file);
    readFile(file, text => {
      try { setMapping(parseCSV(text)); }
      catch (e) { console.error(e); alert('Could not parse Serving base with allocated directors.csv'); }
    });
  }

  // Derive directors and per-director serving girls from mapping
  const directors = useMemo(() => {
    const set = new Set(mapping.map(r => r.Director).filter(Boolean));
    return Array.from(set).sort();
  }, [mapping]);

  const servingByDirector = useMemo(() => {
    const m = {};
    for (const r of mapping) {
      const d = r.Director?.trim();
      const s = (r['Serving Girl'] ?? '').trim();
      if (!d || !s) continue;
      if (!m[d]) m[d] = [];
      if (!m[d].includes(s)) m[d].push(s);
    }
    // Sort for nice UX
    for (const k of Object.keys(m)) m[k].sort();
    return m;
  }, [mapping]);

  // Helpers for conditional logic
  function getYesCount(ids) {
    return ids.reduce((acc, qid) => acc + ((answers[qid] ?? '').toLowerCase() === 'yes' ? 1 : 0), 0);
  }

  function shouldShow(question) {
    const dep = (question['DependsOn'] ?? '').trim();
    const cond = (question['Show Condition'] ?? '').trim();
    if (!dep && !cond) return true; // no condition

    // Handle Q2 logic: DependsOn=Q1, Show Condition like: director={{answer}}
    if (dep && !cond) { // simply depends on an earlier answer existing
      return (answers[dep] ?? '') !== '';
    }

    // Basic condition parser for two patterns used in your CSV:
    // 1) "director={{answer}}" (interprets as show if Q1 (director) has an answer)
    // 2) "yes_count<2" across DependsOn list
    if (cond.includes('yes_count')) {
      const ids = dep.split(',').map(s => s.trim()).filter(Boolean);
      const yc = getYesCount(ids);
      const [lhs, op, rhs] = cond.match(/(yes_count)\s*([<>]=?|==)\s*(\d+)/).slice(1);
      const n = parseInt(rhs, 10);
      if (op === '<') return yc < n;
      if (op === '<=') return yc <= n;
      if (op === '>') return yc > n;
      if (op === '>=') return yc >= n;
      if (op === '==') return yc === n;
      return true;
    }

    if (cond.includes('director={{answer}}')) {
      // Show when the dependent question has any answer
      return (answers[dep] ?? '') !== '';
    }

    return true; // default to visible
  }

  function updateAnswer(qid, value) {
    setAnswers(prev => ({ ...prev, [qid]: value }));
  }

  function validate() {
    const e = {};
    // Require Q1 and Q2
    if (!answers.Q1) e.Q1 = 'Please select a director.';
    if (!answers.Q2) e.Q2 = 'Please select your name.';
    // If Q7 is visible, require non-empty reason
    const q7 = questions.find(q => q.QuestionID === 'Q7');
    if (q7 && shouldShow(q7) && !(answers.Q7 && answers.Q7.trim().length >= 5)) {
      e.Q7 = 'Please provide a brief reason (at least 5 characters).';
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!validate()) return;
    setSubmitted(true);
  }

  const availabilityIds = useMemo(() =>
    questions
      .filter(q => (q.Options?.toLowerCase?.() ?? q['Options Source']?.toLowerCase?.()) === 'yes_no')
      .map(q => q.QuestionID),
    [questions]
  );

  // UI helpers
  const Section = ({ title, children }) => (
    <div className="mt-6">
      <h2 className="text-xl font-semibold mb-3">{title}</h2>
      <div className="grid gap-3">{children}</div>
    </div>
  );

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-6">
      <div className="max-w-3xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">Availability Form (CSV-powered)</h1>
          <p className="text-sm text-gray-600 mt-1">Upload your two CSVs: <em>Form questions.csv</em> and <em>Serving base with allocated directors.csv</em>.</p>
        </div>

        <div className="bg-white rounded-2xl shadow p-5 grid gap-4">
          <div className="grid md:grid-cols-2 gap-4">
            <label className="block">
              <span className="text-sm font-medium">Form questions.csv</span>
              <input type="file" accept=".csv" className="mt-1 block w-full border rounded-lg p-2"
                     onChange={e => e.target.files?.[0] && handleLoadFormQuestions(e.target.files[0])} />
              {formQuestionsFile && <p className="text-xs text-gray-500 mt-1">Loaded: {formQuestionsFile.name}</p>}
            </label>
            <label className="block">
              <span className="text-sm font-medium">Serving base with allocated directors.csv</span>
              <input type="file" accept=".csv" className="mt-1 block w-full border rounded-lg p-2"
                     onChange={e => e.target.files?.[0] && handleLoadServingBase(e.target.files[0])} />
              {servingBaseFile && <p className="text-xs text-gray-500 mt-1">Loaded: {servingBaseFile.name}</p>}
            </label>
          </div>

          {questions.length > 0 && (
            <form onSubmit={handleSubmit} className="mt-2">
              <Section title="Your details">
                {/* Q1 Director */}
                <div>
                  <label className="text-sm font-medium">Please select your director’s name</label>
                  <select
                    className="mt-1 w-full border rounded-lg p-2"
                    value={answers.Q1 || ''}
                    onChange={e => {
                      updateAnswer('Q1', e.target.value);
                      // Reset Q2 if director changes
                      updateAnswer('Q2', '');
                    }}
                  >
                    <option value="">-- Select director --</option>
                    {directors.map(d => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                  {errors.Q1 && <p className="text-sm text-red-600 mt-1">{errors.Q1}</p>}
                </div>

                {/* Q2 Serving Girl (depends on Q1) */}
                {answers.Q1 && (
                  <div>
                    <label className="text-sm font-medium">Please select your name</label>
                    <select
                      className="mt-1 w-full border rounded-lg p-2"
                      value={answers.Q2 || ''}
                      onChange={e => updateAnswer('Q2', e.target.value)}
                    >
                      <option value="">-- Select your name --</option>
                      {(servingByDirector[answers.Q1] || []).map(s => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                    {errors.Q2 && <p className="text-sm text-red-600 mt-1">{errors.Q2}</p>}
                  </div>
                )}
              </Section>

              {/* Availability section */}
              <Section title="Availability in October">
                {questions.filter(q => (q['Options Source'] ?? '').toLowerCase() === 'yes_no').map(q => (
                  <div key={q.QuestionID}>
                    <p className="text-sm font-medium">{q.QuestionText}</p>
                    <div className="mt-1 flex gap-4">
                      {['Yes','No'].map(v => (
                        <label key={v} className="inline-flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name={q.QuestionID}
                            value={v}
                            checked={(answers[q.QuestionID] || '') === v}
                            onChange={() => updateAnswer(q.QuestionID, v)}
                          />
                          <span>{v}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </Section>

              {/* Q7 conditional reason */}
              {(() => {
                const q7 = questions.find(q => q.QuestionID === 'Q7');
                if (!q7) return null;
                if (!shouldShow(q7)) return null;
                return (
                  <Section title="Additional details">
                    <div>
                      <label className="text-sm font-medium">{q7.QuestionText}</label>
                      <textarea
                        className="mt-1 w-full border rounded-lg p-2 min-h-[90px]"
                        value={answers.Q7 || ''}
                        onChange={e => updateAnswer('Q7', e.target.value)}
                        placeholder="Provide a brief reason"
                      />
                      {errors.Q7 && <p className="text-sm text-red-600 mt-1">{errors.Q7}</p>}
                    </div>
                  </Section>
                );
              })()}

              {/* Summary */}
              <div className="mt-6 bg-gray-100 rounded-xl p-4">
                <h3 className="font-semibold mb-2">Live summary</h3>
                <ul className="text-sm grid gap-1">
                  <li><strong>Director:</strong> {answers.Q1 || '—'}</li>
                  <li><strong>Name:</strong> {answers.Q2 || '—'}</li>
                  <li><strong>Yes count:</strong> {getYesCount(availabilityIds)}</li>
                </ul>
              </div>

              <div className="mt-6 flex gap-3">
                <button type="submit" className="px-4 py-2 rounded-xl bg-blue-600 text-white shadow">Submit</button>
                <button type="button" className="px-4 py-2 rounded-xl border"
                  onClick={() => { setAnswers({}); setErrors({}); setSubmitted(false); }}>Reset</button>
              </div>
            </form>
          )}
        </div>

        {submitted && (
          <div className="mt-6 bg-white rounded-2xl shadow p-5">
            <h2 className="text-xl font-semibold mb-2">Submission payload</h2>
            <pre className="text-xs bg-gray-900 text-gray-100 p-3 rounded-lg overflow-auto">{JSON.stringify({
              director: answers.Q1 || null,
              servingGirl: answers.Q2 || null,
              availability: Object.fromEntries(availabilityIds.map(id => [id, answers[id] || null])),
              reason: answers.Q7 || null
            }, null, 2)}</pre>
          </div>
        )}

        <div className="mt-8 text-xs text-gray-500">
          <p><strong>Notes:</strong> This demo understands the specific logic in your CSVs (cascading Director → Serving Girl; Q7 shown when fewer than 2 "Yes" across the availability questions). For more complex conditions, extend <code>shouldShow()</code>.</p>
        </div>
      </div>
    </div>
  );
}
