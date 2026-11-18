/* Wheel of Choice — tiny shared DOM helpers. */

// A small ghost button for the action column of a list row.
export function actionBtn(label, title, onClick) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn-ghost btn-small';
  btn.textContent = label;
  btn.title = title;
  btn.addEventListener('click', onClick);
  return btn;
}
