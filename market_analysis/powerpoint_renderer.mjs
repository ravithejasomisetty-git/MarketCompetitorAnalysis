// Presentation renderer called by presentation_export.py, not by the browser.
// Input: the shared slide-content JSON and a provisioned Artifact Tool module path.
// Output: an editable .pptx file. This process does not research or summarize facts;
// Python has already selected and shortened the displayed content to six slides.
// Source evidence and source speaker notes are intentionally absent from the deck.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

// Python passes these file paths as command-line arguments. The optional last path
// produces PNG previews for development checks; normal app downloads do not need it.
const [modulePath, inputPath, outputPath, renderDirectory] = process.argv.slice(2);
const {Presentation, PresentationFile} = await import(pathToFileURL(modulePath).href);
const slides = JSON.parse(await fs.readFile(inputPath, 'utf8'));
if (slides.length > 6) throw new Error('Market presentations must not exceed six slides.');
// Coordinates use pixels on a 16:9 canvas; PowerPoint converts them to its document units.
const presentation = Presentation.create({slideSize: {width: 1280, height: 720}});

function text(slide, value, left, top, width, height, size, color, bold = false) {
  // Create a native text box, so users can edit its contents after downloading the deck.
  const shape = slide.shapes.add({geometry: 'textbox',
    position: {left, top, width, height}, fill: 'none',
    line: {fill: 'none', width: 0, style: 'solid'}});
  shape.text = value;
  shape.text.style = {typeface: 'Arial', fontSize: size, color, bold};
}

for (const [index, content] of slides.entries()) {
  const slide = presentation.slides.add();
  slide.background.fill = '#F8FAFC';
  text(slide, content.label, 56, 32, 1168, 26, 14, '#52677C', true);
  text(slide, content.title, 56, 82, 1168, 112, content.title.length > 75 ? 30 : 36, '#14283E', true);
  const compact = content.sections.length > 2;
  for (const [sectionIndex, section] of content.sections.entries()) {
    // Product and comparison slides use four sections in a two-by-two layout.
    const left = 56 + (sectionIndex % 2) * 600;
    const top = compact ? 202 + Math.floor(sectionIndex / 2) * 225 : 222;
    text(slide, section.heading, left, top, 560, compact ? 40 : 70, compact ? 22 : 24, '#087E8B', true);
    text(slide, section.paragraphs.join('\n\n'), left, top + (compact ? 48 : 82), 560, compact ? 162 : 338, compact ? 20 : 22, '#14283E');
  }
  text(slide, `${index + 1} / ${slides.length}`, 56, 672, 1168, 24, 14, '#52677C');
  if (renderDirectory) {
    await fs.mkdir(renderDirectory, {recursive: true});
    const png = await presentation.export({slide, format: 'png', scale: 1});
    await fs.writeFile(path.join(renderDirectory, `slide-${String(index + 1).padStart(2, '0')}.png`), new Uint8Array(await png.arrayBuffer()));
  }
}
// Export only the saved findings; this renderer never calls an AI or research service.
await (await PresentationFile.exportPptx(presentation)).save(outputPath);
