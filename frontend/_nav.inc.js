async function initShell(allowedByRole){
  const user=(await authed());
  if(!user){location.href='/auth.html';return null;}
  $('#plan-badge').textContent=user.plan;
  if(user.role==='admin')$('#admin-link').style.display='';
  if(allowedByRole && window.pageRole && user.role!=='admin'){allowedByRole();}
  return user;
}
