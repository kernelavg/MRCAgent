'use strict';
const demoData = {
  projects:[
    {id:'demo-design',name:'Демо · Приложение для задач',status:'running',agents_done:3,agents_total:5,tokens:25420,progress:.6},
    {id:'demo-shop',name:'Демо · Интернет-магазин',status:'idle',agents_done:0,agents_total:4,tokens:0,progress:0}
  ],
  questions:[{id:'demo-question',project_id:'demo-design',question:'Какой стиль интерфейса выбрать для приложения?',suggestion:'Тёмный интерфейс с фиолетовыми акцентами',agent_name:'Агент-дизайнер',project_name:'Демо · Приложение для задач',status:'pending',deadline:Date.now()/1000+86400}],
  agents:[
    {id:'demo-a',nick:'Планирующий шаги',role:'Планировщик',status:'done'},
    {id:'demo-b',nick:'Создающий интерфейс',role:'Дизайнер',status:'done'},
    {id:'demo-c',nick:'Пишущий код',role:'Программист',status:'working'},
    {id:'demo-d',nick:'Проверяющий работу',role:'Тестировщик',status:'working'},
    {id:'demo-e',nick:'Главный агент',role:'Главный',status:'done'}
  ],
  events:[{id:1,text:'Демо: команда получила задачу'},{id:2,text:'Демо: план проекта подготовлен'},{id:3,text:'Демо: агент создаёт интерфейс'}],
  builds:[{status:'done',target:'Android · демо',log:'ДЕМО: пример журнала сборки\nПроверка окружения\nПодготовка проекта\nПоказ интерфейса завершён',message:'Это пример. Настоящий APK не собирался.',artifacts:[]}]
};
function demoResponse(data,status=200){return Promise.resolve(new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}}));}
window.fetch = function(input, options) {
  const path=typeof input==='string'?input:'';
  const method=(options && options.method)||'GET';
  const clean=path.split('?')[0];
  if(clean==='/api/bootstrap')return demoResponse({ok:true,user:{name:'Демонстрация'}});
  if(clean==='/api/projects')return demoResponse({ok:true,projects:demoData.projects});
  if(clean==='/api/questions')return demoResponse({ok:true,questions:demoData.questions});
  const p=clean.split('/');
  if(p[1]==='api' && p[2]==='projects' && p[3]) {
    const project=demoData.projects.find(x=>x.id===decodeURIComponent(p[3]));
    if(!project)return demoResponse({ok:false,error:'Демо-проект не найден'},404);
    if(p.length===4)return demoResponse({ok:true,project,agents:demoData.agents.slice(0,project.agents_total)});
    if(p[4]==='events'){
      const query=new URL(path,location.origin).searchParams;
      const since=Number(query.get('since') || 0);
      return demoResponse({ok:true,events:demoData.events.filter(e=>e.id>since)});
    }
    if(method==='POST' && ['start','pause','resume','stop'].includes(p[4])){
      const statuses={start:'running',pause:'paused',resume:'running',stop:'stopped'};
      project.status=statuses[p[4]];
      demoData.events.push({id:demoData.events.length+1,text:'Демо: команда «'+p[4]+'» показана без подключения к ПК'});
      return demoResponse({ok:true});
    }
  }
  if(p[1]==='api' && p[2]==='ide' && p[4]==='builds')return demoResponse({ok:true,builds:demoData.builds});
  if(p[1]==='api' && p[2]==='questions' && p[4]==='answer' && method==='POST'){
    const q=demoData.questions.find(x=>x.id===p[3]);if(q)q.status='answered';return demoResponse({ok:true});
  }
  return demoResponse({ok:false,error:'В демонстрации это действие недоступно'},404);
};
